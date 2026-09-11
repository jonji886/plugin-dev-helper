"""
Plugin Developer MCP Server

把既有 SDK AST / Knowledge Index / Hybrid Retrieval / Dependency Graph 暴露为
MCP Tools，供本地 Coding Agent 在 Streamable HTTP 上远程调用。
"""

from mcp_server.runtime import Container, build_container
from mcp_server.server import create_app, create_mcp_server, main

__all__ = ["create_app", "create_mcp_server", "build_container", "Container", "main"]
