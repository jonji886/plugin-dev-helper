"""Task Runtime 的核心数据模型。

所有模型均为轻量 dataclass，便于序列化为 JSON 持久化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskStep:
    step_id: str
    task_id: str
    run_id: str
    step_type: str  # generate / validate / repair / build / verify / complete
    status: str = "PENDING"
    started_at: str = ""
    completed_at: str = ""
    input_summary: str = ""
    output_summary: str = ""
    error: str = ""
    artifact_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "step_type": self.step_type,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "error": self.error,
            "artifact_version": self.artifact_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskStep":
        return cls(**data)


@dataclass
class ArtifactVersion:
    version: str  # v0 / v1 / v2 ...
    parent_version: str
    reason: str  # generated / repair-1 / repair-2
    created_at: str = field(default_factory=_now)
    files: dict[str, str] = field(default_factory=dict)  # 相对路径 -> 内容

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "parent_version": self.parent_version,
            "reason": self.reason,
            "created_at": self.created_at,
            "files": self.files,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactVersion":
        return cls(
            version=data["version"],
            parent_version=data.get("parent_version", ""),
            reason=data.get("reason", ""),
            created_at=data.get("created_at", ""),
            files=data.get("files", {}),
        )


@dataclass
class Checkpoint:
    checkpoint_id: str
    task_id: str
    run_id: str
    status: str  # TaskStatus
    current_step: str
    artifact_version: str
    completed_steps: list[str]
    repair_attempt: int
    next_action: str
    created_at: str = field(default_factory=_now)
    last_validation_result_ref: str = ""  # 关联 validation result（按 run 存储）

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status,
            "current_step": self.current_step,
            "artifact_version": self.artifact_version,
            "completed_steps": self.completed_steps,
            "repair_attempt": self.repair_attempt,
            "next_action": self.next_action,
            "created_at": self.created_at,
            "last_validation_result_ref": self.last_validation_result_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        return cls(
            checkpoint_id=data["checkpoint_id"],
            task_id=data["task_id"],
            run_id=data["run_id"],
            status=data["status"],
            current_step=data["current_step"],
            artifact_version=data["artifact_version"],
            completed_steps=data.get("completed_steps", []),
            repair_attempt=data.get("repair_attempt", 0),
            next_action=data.get("next_action", ""),
            created_at=data.get("created_at", ""),
            last_validation_result_ref=data.get("last_validation_result_ref", ""),
        )


@dataclass
class TraceEvent:
    event_id: str
    task_id: str
    run_id: str
    event_type: str
    timestamp: str = field(default_factory=_now)
    step_id: str = ""
    artifact_version: str = ""
    status: str = ""
    duration_ms: float = 0.0
    input_summary: str = ""
    output_summary: str = ""
    error_type: str = ""
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "step_id": self.step_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        return cls(**data)


@dataclass
class FailureRecord:
    task_id: str
    run_id: str
    failure_stage: str  # generate / validate / repair / build
    failure_type: str  # ErrorTaxonomy
    failure_code: str  # issue code 或异常类型
    evidence: str
    recoverable: bool  # RETRYABLE / REPAIRABLE -> True
    recommended_action: str  # retry / repair / abort
    first_divergence: bool = False  # 是否为首个有意义的偏差点
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "failure_stage": self.failure_stage,
            "failure_type": self.failure_type,
            "failure_code": self.failure_code,
            "evidence": self.evidence,
            "recoverable": self.recoverable,
            "recommended_action": self.recommended_action,
            "first_divergence": self.first_divergence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailureRecord":
        return cls(**data)


@dataclass
class Task:
    task_id: str
    run_id: str
    status: str
    current_step: str = ""
    input: str = ""
    workspace: str = ""
    artifact_version: str = "v0"
    repair_attempt: int = 0
    max_repair_attempts: int = 2
    build_verified: bool = False  # Build Gate 是否真正执行并通过；未配置 build 命令时为 False
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status,
            "current_step": self.current_step,
            "input": self.input,
            "workspace": self.workspace,
            "artifact_version": self.artifact_version,
            "repair_attempt": self.repair_attempt,
            "max_repair_attempts": self.max_repair_attempts,
            "build_verified": self.build_verified,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            task_id=data["task_id"],
            run_id=data["run_id"],
            status=data["status"],
            current_step=data.get("current_step", ""),
            input=data.get("input", ""),
            workspace=data.get("workspace", ""),
            artifact_version=data.get("artifact_version", "v0"),
            repair_attempt=data.get("repair_attempt", 0),
            max_repair_attempts=data.get("max_repair_attempts", 2),
            build_verified=bool(data.get("build_verified", False)),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            last_error=data.get("last_error", ""),
        )


# ---------------------------------------------------------------------------
# Repair Context：仅携带修复所需最小必要上下文（evidence-driven）
# ---------------------------------------------------------------------------

from mcp_server.services.project_validator import Issue  # noqa: E402


@dataclass
class RepairContext:
    task_id: str
    goal: str
    current_files: dict[str, str]  # 相对路径 -> 内容
    repairable_issues: list[Issue] = field(default_factory=list)
    evidence: str = ""  # 来自 Validator 的 rule / API 证据摘要
    previous_repair_summary: str = ""  # 上一次修复做了什么（避免重复）

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "current_files": self.current_files,
            "repairable_issues": [i.to_dict() for i in self.repairable_issues],
            "evidence": self.evidence,
            "previous_repair_summary": self.previous_repair_summary,
        }
