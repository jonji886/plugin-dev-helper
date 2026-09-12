"""酷家乐 Rule Engine：加载、过滤和排序结构化规则。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from mcp_server.rules.models import Rule

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SUPPORTED_COMPONENTS = {
    "all",
    "manifest",
    "ui",
    "vm",
    "communication",
    "api",
    "dev_server",
    "network",
}


class KujialeRuleEngine:
    """只读加载酷家乐规则，并提供面向 MCP/Validator 的查询接口。"""

    def __init__(self, rules_path: str | Path | None = None):
        project_root = Path(__file__).resolve().parents[2]
        self.rules_path = Path(rules_path) if rules_path else project_root / "rules" / "kujiale"
        self._rules: list[Rule] | None = None

    def reload(self) -> None:
        self._rules = None

    def rules(self) -> list[Rule]:
        if self._rules is None:
            self._rules = self._load()
        return list(self._rules)

    def get(self, rule_id: str) -> Rule | None:
        return next((rule for rule in self.rules() if rule.id == rule_id), None)

    def query(self, component: str = "all", task: str = "", limit: int = 8) -> list[Rule]:
        """按组件和任务筛选规则，critical/high 优先且默认限制返回数量。"""
        component = (component or "all").strip().lower()
        if component not in SUPPORTED_COMPONENTS:
            raise ValueError(f"不支持的插件组件 `{component}`，可选值：{', '.join(sorted(SUPPORTED_COMPONENTS))}")

        candidates = [rule for rule in self.rules() if rule.matches_scope(component)]
        if not candidates:
            return []

        task_text = (task or "").strip().lower()
        tokens = self._task_tokens(task_text)

        def relevance(rule: Rule) -> int:
            if not tokens or not rule.keywords:
                return 0
            return sum(1 for keyword in rule.keywords if keyword.lower() in task_text or keyword.lower() in tokens)

        # 任务相关性先用于同一严重级别内排序；规则不存在关键词时仍会返回
        # 通用平台约束，避免任务文本过于具体导致关键约束丢失。
        candidates.sort(key=lambda rule: (SEVERITY_ORDER.get(rule.severity, 9), -relevance(rule), rule.id))
        safe_limit = max(1, min(int(limit), len(candidates)))
        return candidates[:safe_limit]

    def categories(self) -> dict[str, int]:
        return {
            rule.id: SEVERITY_ORDER.get(rule.severity, 9)
            for rule in self.rules()
        }

    @staticmethod
    def _task_tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text) if token}

    def _load(self) -> list[Rule]:
        if not self.rules_path.exists():
            return []
        loaded: list[Rule] = []
        for path in sorted(self.rules_path.glob("*.yaml")):
            try:
                raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as error:
                raise ValueError(f"无法加载规则文件 {path}: {error}") from error
            values = raw.get("rules", raw) if isinstance(raw, dict) else raw
            if not isinstance(values, list):
                raise ValueError(f"规则文件 {path} 必须包含 rules 列表")
            for item in values:
                if not isinstance(item, dict):
                    raise ValueError(f"规则文件 {path} 包含无效规则项")
                loaded.append(Rule.model_validate(item))
        ids = [rule.id for rule in loaded]
        if len(ids) != len(set(ids)):
            raise ValueError("酷家乐规则 ID 必须唯一")
        return loaded
