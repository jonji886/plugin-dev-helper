"""probe_plugin_dev_server：探测酷家乐插件本地 HTTP Server。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp_server.services.dev_server import DEFAULT_ORIGIN, DEFAULT_SERVER_URL


def register(mcp, container) -> None:
    @mcp.tool(
        name="probe_plugin_dev_server",
        description=(
            "探测酷家乐插件本地 HTTP Server：manifest.json、manifest.frame 对应 HTML、"
            "manifest.main 对应 VM JavaScript、CORS 和 OPTIONS 预检。默认只探测已启动服务；"
            "start_server=true 时显式执行 npm start，并在探测后回收进程。"
        ),
    )
    async def probe_plugin_dev_server(
        platform: Annotated[str, Field(description="平台，目前仅支持 kujiale", min_length=1, max_length=40)] = "kujiale",
        path: Annotated[str, Field(description="酷家乐工具插件项目目录路径", min_length=1, max_length=2000)] = ".",
        server_url: Annotated[str, Field(description="本地 HTTP Server 地址，默认 http://127.0.0.1:8082", max_length=500)] = DEFAULT_SERVER_URL,
        start_server: Annotated[bool, Field(description="是否显式执行 npm start 后探测；默认 false")]=False,
        timeout_seconds: Annotated[float, Field(description="探测超时时间（秒）", ge=1, le=60)] = 10.0,
        origin: Annotated[str, Field(description="用于 CORS 检查的浏览器 Origin", max_length=500)] = DEFAULT_ORIGIN,
    ) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            if platform.strip().lower() != "kujiale":
                return {
                    "success": False,
                    "status": "not_supported",
                    "message": "当前 Probe 仅支持 kujiale 工具插件。",
                    "data": {"findings": [], "checks": []},
                }
            data = container.dev_server.probe(
                path,
                server_url=server_url,
                start_server=start_server,
                timeout_seconds=timeout_seconds,
                origin=origin,
            )
            return {
                "success": True,
                "status": data["status"],
                "data": data,
            }

        return container.telemetry.guarded("probe_plugin_dev_server", build, query=path)
