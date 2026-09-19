"""get_plugin_scaffold：返回最小酷家乐工具插件骨架。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field


def register(mcp, container) -> None:
    @mcp.tool(
        name="get_plugin_scaffold",
        description=(
            "返回酷家乐工具插件的最小合法骨架、可通过 npm start 启动的本地 HTTP Server、"
            "UI/VM 职责边界与合法 postMessage Pattern。开发插件时需根据用户选择的技术栈传入 stack 参数："
            "vanilla（默认，原生 HTML 实现：manifest.json/page.html/page.js/vm.js，与官方 miniapp-template 一致）"
            "与 react-ts-webpack（React 17 + TS + Webpack 5 实现：manifest.json/src/main.ts/src/view.tsx/webpack.config.js）。"
            "只提供可扩展模板，不生成完整业务代码。"
        ),
    )
    async def get_plugin_scaffold(
        platform: Annotated[str, Field(description="平台，目前仅支持 kujiale", min_length=1, max_length=40)] = "kujiale",
        plugin_type: Annotated[str, Field(description="插件类型，目前仅支持 tool_plugin")] = "tool_plugin",
        task: Annotated[str, Field(description="插件任务，用于返回相关约束", max_length=500)] = "",
        stack: Annotated[str, Field(description="技术栈：vanilla（默认，原生 HTML 实现）或 react-ts-webpack（React 17 + TS + Webpack 5 实现）")] = "vanilla",
    ) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            if platform.strip().lower() != "kujiale" or plugin_type.strip().lower() != "tool_plugin":
                return {
                    "success": False,
                    "status": "not_supported",
                    "message": "当前仅支持 kujiale 的 tool_plugin。",
                    "data": {},
                }
            result = container.scaffold.build(task, stack=stack)
            if not result.get("success", True):
                return result
            return {
                "success": True,
                "status": "ok",
                "data": result,
            }

        return container.telemetry.guarded("get_plugin_scaffold", build, query=task or plugin_type)
