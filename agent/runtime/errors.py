"""错误分类与重试策略：Retry 必须依据 Error Taxonomy，而非裸 except。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorTaxonomy(str, Enum):
    """失败分类（机器可读）。用于 Root Cause Analysis。"""

    RETRIEVAL = "RETRIEVAL"
    KNOWLEDGE_MISSING = "KNOWLEDGE_MISSING"
    API_HALLUCINATION = "API_HALLUCINATION"
    API_PARAMETER = "API_PARAMETER"
    RULE_VIOLATION = "RULE_VIOLATION"
    MANIFEST = "MANIFEST"
    PERMISSION = "PERMISSION"
    SDK_VERSION = "SDK_VERSION"
    MODEL = "MODEL"
    PROVIDER = "PROVIDER"
    MCP = "MCP"
    VALIDATION = "VALIDATION"
    BUILD = "BUILD"
    RUNTIME = "RUNTIME"
    CHECKPOINT = "CHECKPOINT"
    UNKNOWN = "UNKNOWN"


class FailureClass(str, Enum):
    RETRYABLE = "RETRYABLE"
    REPAIRABLE = "REPAIRABLE"
    NON_RETRYABLE = "NON_RETRYABLE"


# 可重试的 Taxonomy（瞬时错误）：LLM 超时、Provider 5xx、MCP 超时、临时网络
_RETRYABLE = {
    ErrorTaxonomy.MODEL,
    ErrorTaxonomy.PROVIDER,
    ErrorTaxonomy.MCP,
    ErrorTaxonomy.RUNTIME,
}

# 可修复的 Taxonomy（需 Repair Loop）：API/规则/Manifest/构建/类型
_REPAIRABLE = {
    ErrorTaxonomy.API_HALLUCINATION,
    ErrorTaxonomy.API_PARAMETER,
    ErrorTaxonomy.RULE_VIOLATION,
    ErrorTaxonomy.MANIFEST,
    ErrorTaxonomy.SDK_VERSION,
    ErrorTaxonomy.VALIDATION,
    ErrorTaxonomy.BUILD,
}

# 不可重试也不可自动修复：需求非法、不支持操作、权限、配置
_NON_RETRYABLE = {
    ErrorTaxonomy.PERMISSION,
    ErrorTaxonomy.RETRIEVAL,
    ErrorTaxonomy.KNOWLEDGE_MISSING,
    ErrorTaxonomy.UNKNOWN,
}


def classify_failure(taxonomy: ErrorTaxonomy) -> FailureClass:
    if taxonomy in _RETRYABLE:
        return FailureClass.RETRYABLE
    if taxonomy in _REPAIRABLE:
        return FailureClass.REPAIRABLE
    return FailureClass.NON_RETRYABLE


@dataclass
class RetryPolicy:
    """基于 Taxonomy 与次数 + 退避的重试策略。"""

    max_attempts: int = 3
    backoff_base_seconds: float = 1.0
    backoff_factor: float = 2.0
    # 仅这些 Taxonomy 可重试
    retryable: set[ErrorTaxonomy] | None = None

    def __post_init__(self) -> None:
        if self.retryable is None:
            self.retryable = set(_RETRYABLE)

    def should_retry(self, attempt: int, taxonomy: ErrorTaxonomy) -> bool:
        """attempt 为已执行次数（从 1 开始）。"""
        if taxonomy not in self.retryable:
            return False
        return attempt < self.max_attempts

    def next_backoff_seconds(self, attempt: int) -> float:
        """attempt 为即将进行的第几次重试（从 1 开始）。"""
        return self.backoff_base_seconds * (self.backoff_factor ** (attempt - 1))
