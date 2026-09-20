"""Task Trace 记录与 Root Cause Analysis。

Trace 层级：Task → Run → Step → (Model / MCP / Validator / Build / Artifact)。
Root Cause 遵循「First Divergence Principle」：定位第一个有意义的偏差点，
而不是仅记录最终 symptom（例如 Build Failed 是下游症状，根因应是 API_HALLUCINATION）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.runtime.errors import ErrorTaxonomy, FailureClass, classify_failure
from agent.runtime.models import FailureRecord, TraceEvent
from agent.runtime.repositories import TaskRepository
from mcp_server.services.project_validator import Issue, ValidationResult


def classify_issue(issue: Issue) -> ErrorTaxonomy:
    """把一条 Validator Issue 映射到 Failure Taxonomy。"""
    if issue.category == "API":
        if issue.code in ("API_UNKNOWN_API", "unknown_api"):
            return ErrorTaxonomy.API_HALLUCINATION
        if issue.code in ("API_UNKNOWN_PARAMETER", "API_MISSING_REQUIRED", "unknown_parameter", "missing_required_field", "missing_required_parameter"):
            return ErrorTaxonomy.API_PARAMETER
        return ErrorTaxonomy.API_PARAMETER
    if issue.category == "MANIFEST":
        return ErrorTaxonomy.MANIFEST
    if issue.category == "RULE":
        return ErrorTaxonomy.RULE_VIOLATION
    if issue.category == "BUILD":
        return ErrorTaxonomy.BUILD
    if issue.category == "STRUCTURE":
        return ErrorTaxonomy.MANIFEST
    return ErrorTaxonomy.VALIDATION


class TaskTraceRecorder:
    """记录 Task / Run / Step 下的 Trace 事件。"""

    def __init__(self, repository: TaskRepository, task_id: str, run_id: str):
        self.repository = repository
        self.task_id = task_id
        self.run_id = run_id

    def record(
        self,
        event_type: str,
        step_id: str = "",
        artifact_version: str = "",
        status: str = "",
        duration_ms: float = 0.0,
        input_summary: str = "",
        output_summary: str = "",
        error_type: str = "",
        error_message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            event_id=uuid4().hex,
            task_id=self.task_id,
            run_id=self.run_id,
            event_type=event_type,
            step_id=step_id,
            artifact_version=artifact_version,
            status=status,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=output_summary,
            error_type=error_type,
            error_message=error_message,
            metadata=metadata or {},
        )
        self.repository.save_trace_event(event)
        return event

    def events(self) -> list[TraceEvent]:
        return self.repository.list_trace_events(self.task_id, self.run_id)


def analyze_root_cause(
    repository: TaskRepository,
    task_id: str,
    run_id: str,
    validation_result: ValidationResult | None = None,
) -> FailureRecord | None:
    """从 Trace + Validator 结果定位首个偏差点（First Divergence）。

    优先依据：
      1) Trace 中最早的异常事件（MODEL / PROVIDER / MCP 等）—— 即时偏差；
      2) Validator 结果中最早出现的 error 级 Issue —— 代码级首次偏差。
    最终返回一个 FailureRecord，标记 first_divergence=True。
    """
    events = repository.list_trace_events(task_id, run_id)

    # 优先定位「Agent 层」偏差：MODEL / PROVIDER / MCP / RETRIEVAL 异常
    first_agent_event = next(
        (e for e in events
         if e.error_type and e.error_type.split("_")[0] in {"MODEL", "PROVIDER", "MCP", "RETRIEVAL"}),
        None,
    )
    if first_agent_event is not None:
        taxonomy = _map_event_error_type(first_agent_event.error_type)
        failure = FailureRecord(
            task_id=task_id, run_id=run_id,
            failure_stage=first_agent_event.event_type,
            failure_type=taxonomy.value,
            failure_code=first_agent_event.error_type,
            evidence=first_agent_event.error_message or first_agent_event.output_summary,
            recoverable=classify_failure(taxonomy) != FailureClass.NON_RETRYABLE,
            recommended_action=_recommend(taxonomy),
            first_divergence=True,
        )
        repository.save_failure(failure)
        return failure

    # 2) Validator 结果中最早的 error 级 Issue（API/RULE/MANIFEST 等才是根因；
    #    BUILD/RUNTIME 失败只是下游 symptom，不应覆盖首个代码级偏差）
    if validation_result is not None and validation_result.errors():
        issues = sorted(
            validation_result.errors(),
            key=lambda i: (i.file, i.line or 0, i.issue_id),
        )
        first = issues[0]
        taxonomy = classify_issue(first)
        failure = FailureRecord(
            task_id=task_id, run_id=run_id,
            failure_stage="validate",
            failure_type=taxonomy.value,
            failure_code=first.code,
            evidence=f"{first.file}:{first.line} {first.message}",
            recoverable=classify_failure(taxonomy) != FailureClass.NON_RETRYABLE,
            recommended_action=_recommend(taxonomy),
            first_divergence=True,
        )
        repository.save_failure(failure)
        return failure

    # 3) 其它 Trace 异常（BUILD / RUNTIME 等下游 symptom）
    first_error_event = next((e for e in events if e.error_type or e.status == "error"), None)
    if first_error_event is not None:
        taxonomy = _map_event_error_type(first_error_event.error_type or "RUNTIME")
        failure = FailureRecord(
            task_id=task_id, run_id=run_id,
            failure_stage=first_error_event.event_type or "runtime",
            failure_type=taxonomy.value,
            failure_code=first_error_event.error_type or "RUNTIME",
            evidence=first_error_event.error_message or first_error_event.output_summary,
            recoverable=classify_failure(taxonomy) != FailureClass.NON_RETRYABLE,
            recommended_action=_recommend(taxonomy),
            first_divergence=True,
        )
        repository.save_failure(failure)
        return failure

    # 3) 若失败但无明确偏差来源
    if any(e.status == "error" for e in events):
        failure = FailureRecord(
            task_id=task_id,
            run_id=run_id,
            failure_stage="unknown",
            failure_type=ErrorTaxonomy.UNKNOWN.value,
            failure_code="UNKNOWN",
            evidence="任务失败但未记录可定位的偏差点。",
            recoverable=False,
            recommended_action="abort",
            first_divergence=True,
        )
        repository.save_failure(failure)
        return failure
    return None


def _map_event_error_type(error_type: str) -> ErrorTaxonomy:
    et = (error_type or "").upper()
    if "MODEL" in et:
        return ErrorTaxonomy.MODEL
    if "PROVIDER" in et:
        return ErrorTaxonomy.PROVIDER
    if "MCP" in et:
        return ErrorTaxonomy.MCP
    if "CHECKPOINT" in et:
        return ErrorTaxonomy.CHECKPOINT
    if "TIMEOUT" in et:
        return ErrorTaxonomy.PROVIDER
    return ErrorTaxonomy.RUNTIME


def _recommend(taxonomy: ErrorTaxonomy) -> str:
    cls = classify_failure(taxonomy)
    if cls == FailureClass.RETRYABLE:
        return "retry"
    if cls == FailureClass.REPAIRABLE:
        return "repair"
    return "abort"
