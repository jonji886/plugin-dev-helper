"""Plugin Developer MCP Server（Streamable HTTP）。

- MCP endpoint：`/mcp`（Streamable HTTP，非已废弃的 SSE-only 架构）
- 健康检查：`GET /health`

Server 只提供可信插件领域知识，不修改 Coding Agent 本地文件，
也不代替 Coding Agent 完成整个项目开发。
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.config import MCPSettings, get_mcp_settings
from mcp_server.runtime import Container, build_container
from mcp_server.tools import TOOL_MODULES, register_tools

SERVICE_NAME = "plugin-developer-mcp"
SERVICE_VERSION = "0.1.0"

INSTRUCTIONS = (
    "Plugin Developer MCP 提供插件 SDK / API / 类型 / 开发文档的结构化查询。"
    "自然语言问题用 search_docs；精确 API 定义用 get_api；构造参数前用 get_type；"
    "展开依赖用 get_related_symbols；找官方示例用 get_examples；"
    "涉及酷家乐工具插件时，开发前用 get_plugin_constraints，必要时用 get_plugin_scaffold，"
    "改完项目用 validate_plugin_project；改完单段 API 代码可用 validate_api_usage 自检。"
    "查不到时会明确返回 not_found，不要臆造 API。"
)


def create_mcp_server(settings: MCPSettings | None = None,
                      container: Container | None = None) -> FastMCP:
    settings = settings or get_mcp_settings()
    resolved_container = container or build_container(settings)
    mcp = FastMCP(
        name=SERVICE_NAME,
        instructions=INSTRUCTIONS,
        host=settings.host,
        stateless_http=settings.stateless_http,
        json_response=os.getenv("MCP_JSON_RESPONSE", "true").strip().lower()
        in {"1", "true", "yes"},
        streamable_http_path=settings.streamable_http_path,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=list(settings.allowed_origins),
        ),
    )
    register_tools(mcp, resolved_container)
    setattr(mcp, "plugin_container", resolved_container)
    return mcp


def _tool_count(mcp: FastMCP) -> int:
    try:
        return len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
    except Exception:
        return len(TOOL_MODULES)


def _health_payload(container: Container, settings: MCPSettings, mcp: FastMCP) -> dict[str, Any]:
    readiness = container.readiness()
    return {
        "status": "ok" if readiness["knowledge_available"] else "degraded",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "mcp_endpoint": settings.streamable_http_path,
        "transport": "streamable-http",
        "tool_count": _tool_count(mcp),
        "telemetry_enabled": settings.telemetry_enabled,
        **readiness,
    }


def create_app(settings: MCPSettings | None = None,
               container: Container | None = None):
    """构建带 /health 的 ASGI 应用（Starlette）。"""
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    settings = settings or get_mcp_settings()
    resolved_container = container or build_container(settings)
    mcp = create_mcp_server(settings, resolved_container)
    app = mcp.streamable_http_app()

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(_health_payload(resolved_container, settings, mcp))

    async def ready(_request: Request) -> JSONResponse:
        payload = _health_payload(resolved_container, settings, mcp)
        status_code = 200 if payload["status"] == "ok" else 503
        return JSONResponse(payload, status_code=status_code)

    app.routes.insert(0, Route("/health", health, methods=["GET"]))
    app.routes.insert(0, Route("/ready", ready, methods=["GET"]))
    setattr(app, "plugin_container", resolved_container)
    return app


def main() -> None:
    import uvicorn

    settings = get_mcp_settings()
    container = build_container(settings)
    try:
        warmup = container.retrieval.warmup()
        print(f"[mcp] embedding warmup: {warmup}")
    except Exception as error:  # 预热失败不阻断启动
        print(f"[mcp] embedding warmup skipped: {error}")
    app = create_app(settings, container)
    print(f"[mcp] listening on http://{settings.host}:{settings.port}{settings.streamable_http_path}")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
