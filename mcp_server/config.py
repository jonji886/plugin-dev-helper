"""Plugin Developer MCP 运行配置。

路径相关配置复用 `app.config.get_settings()`，保证 MCP 与既有 Chat/RAG
服务读取同一份知识库（data/knowledge、data/graph、data/chroma）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _csv_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "[::1]:*")


@dataclass(frozen=True)
class MCPSettings:
    host: str
    port: int
    streamable_http_path: str
    stateless_http: bool
    knowledge_path: Path
    graph_path: Path
    chroma_path: Path
    database_path: Path
    rag_docs_path: Path
    search_top_k: int
    max_search_top_k: int
    max_search_content_chars: int
    max_graph_depth: int
    max_graph_nodes: int
    max_examples: int
    max_example_chars: int
    max_code_chars: int
    max_candidates: int
    telemetry_enabled: bool
    tool_timeout_seconds: float
    default_sdk_version: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]


def get_mcp_settings() -> MCPSettings:
    """从环境变量读取 MCP 配置；知识库路径与既有应用保持一致。"""
    from app.config import get_settings

    settings = get_settings()
    project_root = Path(__file__).resolve().parent.parent
    return MCPSettings(
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=_positive_int("MCP_PORT", 8001),
        streamable_http_path=os.getenv("MCP_STREAMABLE_HTTP_PATH", "/mcp"),
        stateless_http=_env_flag("MCP_STATELESS_HTTP", True),
        knowledge_path=settings.knowledge_path,
        graph_path=settings.graph_path,
        chroma_path=settings.chroma_path,
        database_path=settings.database_path,
        rag_docs_path=Path(os.getenv("RAG_DOCS_PATH", str(project_root / "docs" / "rag"))),
        search_top_k=_positive_int("MCP_SEARCH_TOP_K", 5),
        max_search_top_k=_positive_int("MCP_MAX_SEARCH_TOP_K", 20),
        max_search_content_chars=_positive_int("MCP_SEARCH_CONTENT_CHARS", 1500),
        max_graph_depth=_positive_int("MCP_MAX_GRAPH_DEPTH", 3),
        max_graph_nodes=_positive_int("MCP_MAX_GRAPH_NODES", 50),
        max_examples=_positive_int("MCP_MAX_EXAMPLES", 5),
        max_example_chars=_positive_int("MCP_MAX_EXAMPLE_CHARS", 4000),
        max_code_chars=_positive_int("MCP_MAX_CODE_CHARS", 20000),
        max_candidates=_positive_int("MCP_MAX_CANDIDATES", 5),
        telemetry_enabled=_env_flag("MCP_TELEMETRY_ENABLED", True),
        tool_timeout_seconds=_positive_float("MCP_TOOL_TIMEOUT_SECONDS", 30.0),
        default_sdk_version=os.getenv("MCP_DEFAULT_SDK_VERSION", ""),
        allowed_hosts=_csv_list("MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS),
        allowed_origins=_csv_list("MCP_ALLOWED_ORIGINS", ()),
    )
