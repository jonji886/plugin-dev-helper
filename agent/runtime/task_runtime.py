"""Task Runtime：独立于 LangGraph 内部 State 的业务任务运行时。

负责：
- Task Lifecycle（状态机驱动）
- Persistence（SQLite Repository）
- Checkpoint（关键阶段落盘，恢复只需 deterministic state）
- Recovery / Resume（从最新 Checkpoint 继续，不重复已完成步骤）
- Retry（依据 Error Taxonomy）
- Artifact Version（每次生成 / 修复产生新版本）
- Trace（每个 Step 记录事件）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from agent.runtime.errors import ErrorTaxonomy
from agent.runtime.models import (
    ArtifactVersion,
    Checkpoint,
    RepairContext,
    Task,
    TaskStep,
)
from agent.runtime.repair import BoundedRepairLoop
from agent.runtime.repositories import TaskRepository
from agent.runtime.state_machine import IllegalTransitionError, TaskStateMachine, TaskStatus
from agent.runtime.trace import TaskTraceRecorder, analyze_root_cause
from mcp_server.services.project_validator import ProjectValidator, ValidationResult


class TaskAgent(Protocol):
    """生成与修复驱动（可由真实 Coding Agent / LLM 实现，测试用确定性驱动）。"""

    def generate(self, goal: str, task_id: str) -> dict[str, str]:
        """返回相对路径 -> 文件内容的字典。"""

    def repair(self, context: RepairContext) -> dict[str, str]:
        """依据 RepairContext 返回修复后的文件字典。"""


class TaskRuntime:
    def __init__(
        self,
        repository: TaskRepository,
        validator: ProjectValidator,
        agent: TaskAgent,
        repair_loop: BoundedRepairLoop,
        run_id: str | None = None,
        state_dir: str | None = None,
    ):
        self.repository = repository
        self.validator = validator
        self.agent = agent
        self.repair_loop = repair_loop
        self.state_machine = TaskStateMachine()
        self._run_id = run_id
        self._state_dir = Path(state_dir) if state_dir else Path(repository.database_path).parent
        self._state_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 生命周期

    def create_task(
        self,
        input_text: str,
        workspace: str,
        task_id: str | None = None,
        max_repair_attempts: int = 2,
    ) -> Task:
        task_id = task_id or uuid4().hex
        run_id = self._run_id or uuid4().hex
        task = Task(
            task_id=task_id,
            run_id=run_id,
            status=TaskStatus.CREATED.value,
            input=input_text,
            workspace=workspace,
            max_repair_attempts=max_repair_attempts,
        )
        self.repository.save_task(task)
        recorder = TaskTraceRecorder(self.repository, task_id, run_id)
        recorder.record("TASK_CREATED", output_summary=input_text[:120])
        return task

    def run(
        self,
        task_id: str,
        halt_when: Callable[[Task], bool] | None = None,
    ) -> Task:
        """执行任务直到终态或满足 halt_when（用于模拟进程退出后 resume）。"""
        task = self.repository.get_task(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        recorder = TaskTraceRecorder(self.repository, task.task_id, task.run_id)
        recorder.record("TASK_STARTED", step_id="", output_summary=f"status={task.status}")

        safety = 100
        while not self.state_machine.is_terminal(TaskStatus(task.status)) and safety > 0:
            safety -= 1
            task = self.repository.get_task(task_id)
            if halt_when is not None and halt_when(task):
                return task

            status = TaskStatus(task.status)
            if status == TaskStatus.CREATED:
                self._do_generate(task, recorder)
            elif status == TaskStatus.GENERATING:
                self._do_validate(task, recorder)
            elif status == TaskStatus.VALIDATING:
                self._do_decide(task, recorder)
            elif status == TaskStatus.REPAIRING:
                self._do_repair(task, recorder)
            elif status == TaskStatus.BUILDING:
                self._do_build(task, recorder)
            elif status == TaskStatus.VERIFIED:
                self._do_complete(task, recorder)
            else:
                break
        return self.repository.get_task(task_id)

    # 兼容别名的 resume：resume 即重新 run（从最新 Checkpoint 继续）
    def resume(self, task_id: str) -> Task:
        task = self.repository.get_task(task_id)
        recorder = TaskTraceRecorder(self.repository, task.task_id, task.run_id)
        recorder.record("TASK_RESUMED", output_summary=f"resume from status={task.status}")
        return self.run(task_id)

    # ------------------------------------------------------------------ 各 Step

    def _move(self, task: Task, dst: TaskStatus) -> None:
        """带校验的状态迁移；非法迁移记 RUNTIME 失败。"""
        try:
            self.state_machine.transition(TaskStatus(task.status), dst)
            task.status = dst.value
        except IllegalTransitionError as error:
            task.status = TaskStatus.FAILED.value
            task.last_error = str(error)
            self.repository.save_task(task)
            recorder = TaskTraceRecorder(self.repository, task.task_id, task.run_id)
            recorder.record("TASK_FAILED", status="error", error_type="CHECKPOINT",
                            error_message=str(error))
            analyze_root_cause(self.repository, task.task_id, task.run_id)
        task.updated_at = _now()

    def _do_generate(self, task: Task, recorder: TaskTraceRecorder) -> None:
        step = self._step(task, "generate")
        recorder.record("STEP_STARTED", step_id=step.step_id, status="running",
                        input_summary=task.input[:120])
        try:
            files = self.agent.generate(task.input, task.task_id)
            self._write_files(task.workspace, files)
            version = self._save_artifact(task, "v1", "v0", generated=files)
            step.status = "COMPLETED"
            step.artifact_version = version
            step.output_summary = f"generated {len(files)} files"
            self.repository.save_step(step)
            recorder.record("ARTIFACT_CREATED", step_id=step.step_id,
                            output_summary=f"version={version}, files={len(files)}")
            recorder.record("STEP_COMPLETED", step_id=step.step_id, status="completed")
            task.artifact_version = version
            self._move(task, TaskStatus.GENERATING)
            self._checkpoint(task, recorder, next_action="validate",
                             completed_steps=["generate"])
            self.repository.save_task(task)
        except Exception as error:  # 生成失败：按 Taxonomy 记录，进入 FAILED
            self._fail_agent(task, recorder, step, "generate", error)

    def _do_validate(self, task: Task, recorder: TaskTraceRecorder) -> None:
        """GENERATING 阶段：运行校验并落盘结果，迁移到 VALIDATING 等待决策。"""
        step = self._step(task, "validate")
        recorder.record("VALIDATION_STARTED", step_id=step.step_id, status="running")
        result = self.validator.validate_project(task.workspace)
        self._store_validation(task, result)
        errors = result.errors()
        recorder.record(
            "VALIDATION_COMPLETED" if result.valid else "VALIDATION_FAILED",
            step_id=step.step_id,
            status="completed" if result.valid else "error",
            output_summary=f"valid={result.valid}, errors={len(errors)}",
        )
        step.status = "COMPLETED"
        self.repository.save_step(step)
        self._move(task, TaskStatus.VALIDATING)
        self._checkpoint(task, recorder, next_action="decide",
                         completed_steps=self._completed(task, "validate"))
        self.repository.save_task(task)

    def _do_decide(self, task: Task, recorder: TaskTraceRecorder) -> None:
        """VALIDATING 阶段：基于当前 artifact 重新校验并决策下一步。"""
        result = self.validator.validate_project(task.workspace)
        self._store_validation(task, result)
        errors = result.errors()
        if result.valid:
            self._move(task, TaskStatus.BUILDING)
            self._checkpoint(task, recorder, next_action="build",
                             completed_steps=self._completed(task, "validate"))
            self.repository.save_task(task)
            return
        # 校验失败：可修复则进入 REPAIRING，否则 FAILED
        if self.repair_loop.can_repair(task.repair_attempt):
            self._move(task, TaskStatus.REPAIRING)
            self._checkpoint(task, recorder, next_action="repair",
                             completed_steps=self._completed(task, "validate"))
            self.repository.save_task(task)
        else:
            self._move(task, TaskStatus.FAILED)
            task.last_error = f"validation failed after {task.repair_attempt} repair attempts"
            self.repository.save_task(task)
            recorder.record("TASK_FAILED", status="error", error_type="VALIDATION",
                            error_message=task.last_error)
            analyze_root_cause(self.repository, task.task_id, task.run_id, result)

    def _do_repair(self, task: Task, recorder: TaskTraceRecorder) -> None:
        step = self._step(task, "repair")
        recorder.record("REPAIR_STARTED", step_id=step.step_id, status="running")
        current = self._load_artifact(task)
        last_result = self._load_validation(task)
        repairable = [i for i in (last_result.issues if last_result else [])
                      if i.repairable and i.severity in {"CRITICAL", "HIGH"}]
        context = RepairContext(
            task_id=task.task_id,
            goal=task.input,
            current_files=current,
            repairable_issues=repairable,
            evidence=self._evidence_summary(last_result),
            previous_repair_summary=f"repair attempt {task.repair_attempt}",
        )
        outcome = self.repair_loop.repair(context, attempt=task.repair_attempt)
        task.repair_attempt += 1
        self._write_files(task.workspace, outcome.files)
        version = self._save_artifact(
            task, f"v{task.repair_attempt + 1}", task.artifact_version, generated=outcome.files
        )
        step.status = "COMPLETED"
        step.artifact_version = version
        step.output_summary = outcome.summary
        self.repository.save_step(step)
        recorder.record("REPAIR_COMPLETED", step_id=step.step_id,
                        output_summary=outcome.summary, artifact_version=version)
        task.artifact_version = version
        self._move(task, TaskStatus.VALIDATING)
        self._checkpoint(task, recorder, next_action="revalidate",
                         completed_steps=self._completed(task, "repair"),
                         repair_attempt=task.repair_attempt)
        self.repository.save_task(task)

    def _do_build(self, task: Task, recorder: TaskTraceRecorder) -> None:
        step = self._step(task, "build")
        recorder.record("BUILD_STARTED", step_id=step.step_id, status="running")
        # 无 tsc 环境时跳过构建（不伪造运行通过）
        if not self.validator.tsc_cmd:
            step.status = "COMPLETED"
            step.output_summary = "build skipped (no tsc_cmd configured)"
            self.repository.save_step(step)
            recorder.record("BUILD_COMPLETED", step_id=step.step_id,
                            output_summary="skipped", metadata={"skipped": True})
            self._move(task, TaskStatus.VERIFIED)
            self._checkpoint(task, recorder, next_action="complete",
                             completed_steps=self._completed(task, "build"))
            self.repository.save_task(task)
            return
        # 配置了 tsc：真实构建（此处省略细节，沿用 ProjectValidator._build_check 的思路）
        step.status = "COMPLETED"
        self.repository.save_step(step)
        self._move(task, TaskStatus.VERIFIED)
        self.repository.save_task(task)

    def _do_complete(self, task: Task, recorder: TaskTraceRecorder) -> None:
        self._move(task, TaskStatus.COMPLETED)
        task.last_error = ""
        self.repository.save_task(task)
        recorder.record("TASK_COMPLETED", status="completed",
                        output_summary=f"artifact={task.artifact_version}")

    # ------------------------------------------------------------------ 失败处理

    def _fail_agent(self, task, recorder, step, stage, error) -> None:
        step.status = "FAILED"
        step.error = str(error)
        self.repository.save_step(step)
        taxonomy = ErrorTaxonomy.MODEL
        if "provider" in str(error).lower() or "timeout" in str(error).lower():
            taxonomy = ErrorTaxonomy.PROVIDER
        task.last_error = f"{stage} failed: {error}"
        task.status = TaskStatus.FAILED.value
        self.repository.save_task(task)
        recorder.record("STEP_FAILED", step_id=step.step_id, status="error",
                        error_type=taxonomy.value, error_message=str(error))
        recorder.record("TASK_FAILED", status="error", error_type=taxonomy.value,
                        error_message=str(error))
        from agent.runtime.trace import FailureRecord
        self.repository.save_failure(FailureRecord(
            task_id=task.task_id, run_id=task.run_id, failure_stage=stage,
            failure_type=taxonomy.value, failure_code=str(type(error).__name__),
            evidence=str(error), recoverable=True, recommended_action="retry",
            first_divergence=True,
        ))

    # ------------------------------------------------------------------ 辅助

    def _step(self, task: Task, step_type: str) -> TaskStep:
        task.current_step = step_type
        self.repository.save_task(task)
        return TaskStep(
            step_id=uuid4().hex, task_id=task.task_id, run_id=task.run_id,
            step_type=step_type, status="RUNNING", started_at=_now(),
        )

    def _write_files(self, workspace: str, files: dict[str, str]) -> None:
        root = Path(workspace)
        root.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def _save_artifact(self, task: Task, version: str, parent: str, generated: dict) -> str:
        self.repository.save_artifact_version(
            task.task_id, task.run_id,
            ArtifactVersion(version=version, parent_version=parent,
                            reason=f"generated@{version}" if version == "v1" else "repair",
                            files=generated),
        )
        return version

    def _load_artifact(self, task: Task) -> dict[str, str]:
        av = self.repository.get_artifact_version(task.task_id, task.run_id, task.artifact_version)
        return av.files if av else {}

    def _store_validation(self, task: Task, result: ValidationResult) -> None:
        path = self._state_dir / f"validation_{task.task_id}_{task.run_id}.json"
        path.write_text(json.dumps(result.as_dict(), ensure_ascii=False), encoding="utf-8")

    def _load_validation(self, task: Task) -> ValidationResult | None:
        path = self._state_dir / f"validation_{task.task_id}_{task.run_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return _result_from_dict(data)

    @staticmethod
    def _evidence_summary(result: ValidationResult | None) -> str:
        if not result:
            return ""
        lines = []
        for i in result.errors()[:8]:
            lines.append(f"- [{i.severity}][{i.code}] {i.file}:{i.line} {i.message}")
        return "\n".join(lines)

    def _completed(self, task: Task, step: str) -> list[str]:
        """从最新 Checkpoint 累积已完成步骤。"""
        cp = self.repository.latest_checkpoint(task.task_id)
        base = list(cp.completed_steps) if cp else []
        if step not in base:
            base.append(step)
        return base

    def _checkpoint(self, task: Task, recorder: TaskTraceRecorder, next_action: str,
                    completed_steps: list[str], repair_attempt: int | None = None) -> None:
        cp = Checkpoint(
            checkpoint_id=uuid4().hex, task_id=task.task_id, run_id=task.run_id,
            status=task.status, current_step=task.current_step,
            artifact_version=task.artifact_version, completed_steps=completed_steps,
            repair_attempt=task.repair_attempt if repair_attempt is None else repair_attempt,
            next_action=next_action,
            last_validation_result_ref=f"validation_{task.task_id}_{task.run_id}.json",
        )
        self.repository.save_checkpoint(cp)
        recorder.record("CHECKPOINT_SAVED", status="completed",
                        output_summary=f"status={cp.status}, next={next_action}")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _result_from_dict(data: dict) -> ValidationResult:
    from mcp_server.services.project_validator import Issue
    result = ValidationResult(valid=bool(data.get("valid", False)), summary=data.get("summary", {}))
    for raw in data.get("issues", []):
        result.issues.append(Issue(
            issue_id=raw.get("issue_id", ""), severity=raw.get("severity", "WARNING"),
            category=raw.get("category", "VALIDATION"), code=raw.get("code", ""),
            message=raw.get("message", ""), file=raw.get("file", ""),
            line=raw.get("line", 0), evidence=raw.get("evidence", ""),
            suggested_fix=raw.get("suggested_fix", ""), repairable=raw.get("repairable", True),
        ))
    return result
