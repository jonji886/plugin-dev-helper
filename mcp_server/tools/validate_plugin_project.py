"""validate_plugin_project：扫描酷家乐工具插件项目。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field


def register(mcp, container) -> None:
    @mcp.tool(
        name="validate_plugin_project",
        description=(
            "校验酷家乐工具插件项目目录：manifest、HTML UI、JavaScript VM 及 UI/VM action 通信。"
            "返回 score、按 severity 汇总和带文件/行号/风险/建议/confidence 的 Finding；"
            "存在 Critical Finding 时不能视为完成。"
        ),
    )
    async def validate_plugin_project(
        platform: Annotated[str, Field(description="平台，目前仅支持 kujiale", min_length=1, max_length=40)] = "kujiale",
        path: Annotated[str, Field(description="酷家乐工具插件项目目录路径", min_length=1, max_length=2000)] = ".",
    ) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            if platform.strip().lower() != "kujiale":
                return {
                    "success": False,
                    "status": "not_supported",
                    "message": "当前 Validator 仅支持 kujiale 工具插件。",
                    "data": {"findings": [], "summary": {}, "score": 0},
                }
            data = container.plugin_validator.validate(path)
            return {
                "success": True,
                "status": data["status"],
                "data": data,
            }

        return container.telemetry.guarded("validate_plugin_project", build, query=path)
