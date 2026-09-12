"""MCP Tool 注册入口。"""

from mcp_server.tools import (
    get_api,
    get_examples,
    get_related_symbols,
    get_plugin_constraints,
    get_plugin_scaffold,
    probe_plugin_dev_server,
    get_type,
    search_docs,
    validate_api_usage,
    validate_plugin_project,
)

TOOL_MODULES = (
    search_docs,
    get_api,
    get_type,
    get_related_symbols,
    get_examples,
    validate_api_usage,
    get_plugin_constraints,
    validate_plugin_project,
    get_plugin_scaffold,
    probe_plugin_dev_server,
)

__all__ = ["register_tools", "TOOL_MODULES"]


def register_tools(mcp, container) -> None:
    for module in TOOL_MODULES:
        module.register(mcp, container)
