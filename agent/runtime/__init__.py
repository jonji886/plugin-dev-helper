"""Task Runtime：与 LangGraph 内部 State 解耦的业务任务运行时。

负责 Task Lifecycle / State / Persistence / Checkpoint / Recovery / Retry / Artifact / Trace。
LangGraph 仍可用于 Agent Graph，但整个插件的业务任务状态以本 Runtime 为准。
"""

from agent.runtime.errors import ErrorTaxonomy, FailureClass, RetryPolicy, classify_failure
from agent.runtime.models import (
    ArtifactVersion,
    Checkpoint,
    FailureRecord,
    RepairContext,
    Task,
    TaskStep,
    TraceEvent,
)
from agent.runtime.state_machine import (
    IllegalTransitionError,
    TaskStateMachine,
    TaskStatus,
    TaskStepStatus,
)
from agent.runtime.task_runtime import TaskAgent, TaskRuntime

__all__ = [
    "ErrorTaxonomy",
    "FailureClass",
    "RetryPolicy",
    "classify_failure",
    "ArtifactVersion",
    "Checkpoint",
    "FailureRecord",
    "RepairContext",
    "Task",
    "TaskStep",
    "TraceEvent",
    "IllegalTransitionError",
    "TaskStateMachine",
    "TaskStatus",
    "TaskStepStatus",
    "TaskAgent",
    "TaskRuntime",
    "BoundedRepairLoop",
    "EvidenceBasedRepairDriver",
    "TaskRepository",
    "TaskTraceRecorder",
    "analyze_root_cause",
]
