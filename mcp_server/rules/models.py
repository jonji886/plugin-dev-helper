"""Rule Layer 的稳定数据模型。

规则文件是平台约束的唯一事实来源。MCP Tool 和 Validator 只消费这里的
结构化模型，不在代码中重复维护 severity、风险和修改建议。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RuleSeverity = Literal["critical", "high", "medium", "low"]
RuleScope = Literal[
    "manifest",
    "ui",
    "vm",
    "communication",
    "api",
    "dev_server",
    "network",
]


class Rule(BaseModel):
    """一条可查询、可执行的插件开发约束。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    platform: str = "kujiale"
    category: str
    scope: RuleScope
    severity: RuleSeverity
    title: str
    description: str
    detection: dict[str, object] = Field(default_factory=dict)
    confidence: str = "high"
    suggestion: str = ""
    references: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    required_patterns: list[str] = Field(default_factory=list)
    score_category: str = "runtime"

    def matches_scope(self, component: str) -> bool:
        # 查询侧语义：component 命中 scope 或 category 任一即可。
        # scope 表示静态校验时的文件定位（ui/vm），category 表示约束主题（如 communication）；
        # 两者解耦后，component=communication 仍应返回 UI/VM 两侧的通信约束。
        return component == "all" or self.scope == component or self.category == component

    def as_constraint(self) -> dict[str, object]:
        """返回适合 Coding Agent 消费的精简约束。"""
        return {
            "rule_id": self.id,
            "severity": self.severity,
            "scope": self.scope,
            "title": self.title,
            "rule": self.description,
            "suggestion": self.suggestion,
            "confidence": self.confidence,
            "references": self.references,
        }
