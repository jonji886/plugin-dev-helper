"""get_plugin_constraints：按任务返回酷家乐插件开发约束。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field


def register(mcp, container) -> None:
    @mcp.tool(
        name="get_plugin_constraints",
        description=(
            "在开始开发酷家乐工具插件前获取结构化 Guardrail。按 component/task 过滤，"
            "优先返回 critical/high 约束；规则来自 Rule Layer，不返回大段平台文档。"
        ),
    )
    async def get_plugin_constraints(
        platform: Annotated[str, Field(description="平台，目前仅支持 kujiale", min_length=1, max_length=40)] = "kujiale",
        component: Annotated[str, Field(description="组件：all / manifest / ui / vm / communication / api / dev_server / network")] = "all",
        task: Annotated[str, Field(description="当前插件任务，用于筛选相关约束", max_length=500)] = "",
    ) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            if platform.strip().lower() != "kujiale":
                return {
                    "success": False,
                    "status": "not_supported",
                    "message": "当前 Guardrail 仅支持 kujiale 工具插件。",
                    "data": {"constraints": [], "required_patterns": [], "references": []},
                }
            rules = container.kujiale_rules.query(component, task, limit=8)
            constraints = [rule.as_constraint() for rule in rules]
            required_patterns: list[str] = []
            references: list[str] = []
            for rule in rules:
                for pattern in rule.required_patterns:
                    if pattern not in required_patterns:
                        required_patterns.append(pattern)
                for reference in rule.references:
                    if reference not in references:
                        references.append(reference)
            return {
                "success": True,
                "status": "ok",
                "data": {
                    "platform": "kujiale",
                    "component": component,
                    "task": task,
                    "constraints": constraints,
                    "required_patterns": required_patterns,
                    "references": references,
                },
            }

        return container.telemetry.guarded(
            "get_plugin_constraints", build, query=task or component
        )
