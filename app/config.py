"""应用运行配置。所有可部署差异均通过环境变量声明。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须为正整数")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须为正数")
    return value


@dataclass(frozen=True)
class Settings:
    frontend_origins: list[str]
    database_path: Path
    chroma_path: Path
    knowledge_path: Path
    graph_path: Path
    retrieval_top_k: int
    llm_timeout_seconds: float
    llm_max_retries: int
    langfuse_enabled: bool
    default_llm_provider: str
    default_llm_model: str
    fast_llm_provider: str
    fast_llm_model: str
    strong_llm_provider: str
    strong_llm_model: str
    prompt_version: str


def get_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
    origins = [
        origin.strip()
        for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ]
    if "*" in origins:
        raise ValueError("FRONTEND_ORIGINS 不能包含通配符 *")

    return Settings(
        frontend_origins=origins,
        database_path=Path(os.getenv("APP_DATABASE_PATH", str(data_dir / "app.sqlite3"))),
        chroma_path=Path(os.getenv("CHROMA_PATH", str(data_dir / "chroma"))),
        knowledge_path=Path(os.getenv("KNOWLEDGE_PATH", str(data_dir / "knowledge"))),
        graph_path=Path(os.getenv("GRAPH_PATH", str(data_dir / "graph" / "dependency_graph.json"))),
        retrieval_top_k=_positive_int("RETRIEVAL_TOP_K", 5),
        llm_timeout_seconds=_positive_float("LLM_TIMEOUT_SECONDS", 30.0),
        llm_max_retries=_positive_int("LLM_MAX_RETRIES", 2),
        langfuse_enabled=os.getenv("LANGFUSE_ENABLED", "false").lower() in {"1", "true", "yes"},
        default_llm_provider=os.getenv("DEFAULT_LLM_PROVIDER", "deepseek"),
        default_llm_model=os.getenv("DEFAULT_LLM_MODEL", "deepseek-chat"),
        fast_llm_provider=os.getenv("FAST_LLM_PROVIDER", ""),
        fast_llm_model=os.getenv("FAST_LLM_MODEL", ""),
        strong_llm_provider=os.getenv("STRONG_LLM_PROVIDER", ""),
        strong_llm_model=os.getenv("STRONG_LLM_MODEL", ""),
        prompt_version=os.getenv("PROMPT_VERSION", "v1"),
    )
