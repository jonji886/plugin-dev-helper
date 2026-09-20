"""任务状态机：明确合法迁移，禁止非法 Transition。"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    REPAIRING = "REPAIRING"
    BUILDING = "BUILDING"
    VERIFIED = "VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# 合法迁移表（不含异常态进入；异常态统一从任意活跃态进入 FAILED/CANCELLED）
_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.GENERATING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.GENERATING: {TaskStatus.VALIDATING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.VALIDATING: {
        TaskStatus.BUILDING, TaskStatus.REPAIRING, TaskStatus.FAILED, TaskStatus.CANCELLED,
    },
    TaskStatus.REPAIRING: {TaskStatus.VALIDATING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.BUILDING: {
        TaskStatus.VERIFIED, TaskStatus.REPAIRING, TaskStatus.FAILED, TaskStatus.CANCELLED,
    },
    TaskStatus.VERIFIED: {TaskStatus.COMPLETED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}

_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class IllegalTransitionError(Exception):
    """非法状态迁移。"""


class TaskStateMachine:
    """校验并应用状态迁移。"""

    def __init__(self, transitions: dict[TaskStatus, set[TaskStatus]] | None = None):
        self._transitions = transitions or _TRANSITIONS

    @staticmethod
    def terminal_states() -> set[TaskStatus]:
        return set(_TERMINAL)

    def is_terminal(self, status: TaskStatus) -> bool:
        return status in _TERMINAL

    def can_transition(self, src: TaskStatus, dst: TaskStatus) -> bool:
        if src == dst:
            return False
        allowed = self._transitions.get(src, set())
        return dst in allowed

    def transition(self, src: TaskStatus, dst: TaskStatus) -> TaskStatus:
        if not self.can_transition(src, dst):
            raise IllegalTransitionError(
                f"非法状态迁移：{src.value} -> {dst.value}。"
                f"允许的目标：{{{', '.join(t.value for t in self._transitions.get(src, set()))}}}"
            )
        return dst
