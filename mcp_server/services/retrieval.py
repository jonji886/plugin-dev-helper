"""自然语言检索服务：复用既有 Hybrid Retrieval（语义 + 词法 + 依赖权重）。

不复制检索逻辑，只做一次薄封装：
- 延迟加载 `vector_store.VectorStore`（embedding 模型较重）
- 复用 `agent.assistant.is_overview_query` 判断总览型问题
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from mcp_server.services.common import citation, truncate


class RetrievalService:
    """对 `vector_store.VectorStore.search_hybrid` 的只读封装。"""

    def __init__(self, chroma_path: str | Path, knowledge_path: str | Path):
        self.chroma_path = Path(chroma_path)
        self.knowledge_path = Path(knowledge_path)
        self._store = None
        self._lock = threading.RLock()
        self._available: bool | None = None

    @property
    def store(self):
        with self._lock:
            if self._store is None:
                from vector_store import VectorStore

                self._store = VectorStore(
                    persist_dir=str(self.chroma_path),
                    knowledge_dir=str(self.knowledge_path),
                )
            return self._store

    def is_available(self) -> bool:
        if self._available is None:
            try:
                self._available = self.store.count() > 0
            except Exception:
                self._available = False
        return self._available

    def warmup(self) -> dict[str, Any]:
        try:
            return self.store.warmup()
        except Exception as error:  # 预热失败不应阻断 MCP 启动
            return {"ready": False, "warmup_ms": 0.0, "error": str(error)}

    @staticmethod
    def _boost_overview(query: str) -> bool:
        from agent.assistant import is_overview_query

        return is_overview_query(query)

    def search(self, query: str, top_k: int, boost_overview: bool | None = None) -> list[dict]:
        if boost_overview is None:
            try:
                boost_overview = self._boost_overview(query)
            except Exception:
                boost_overview = False
        return self.store.search_hybrid(query, top_k=top_k, boost_overview=boost_overview)

    def enrich(self, results: list[dict], knowledge, content_chars: int) -> list[dict]:
        """把检索结果装配成带 citation 的结构化结果。"""
        enriched: list[dict] = []
        for result in results:
            metadata = result.get("metadata", {}) or {}
            doc_id = result.get("id") or metadata.get("id", "")
            entry = knowledge.get_entry(doc_id) if doc_id else None
            if entry is None and doc_id:
                resolved = knowledge.resolve(doc_id)
                entry = resolved.entry
            content = result.get("document", "") or ""
            if entry is not None:
                # 向量 chunk 只有 500 字符，正式文档/总览类结果用源文件补充上下文
                markdown = knowledge.markdown(entry)
                if markdown:
                    content = markdown
            score = result.get("hybrid_score")
            if score is None:
                score = result.get("keyword_score")
            item = {
                "symbol": doc_id,
                "title": (entry or metadata).get("name") or doc_id,
                "type": (entry or metadata).get("type", ""),
                "namespace": (entry or metadata).get("namespace", ""),
                "content": truncate(content, content_chars),
                "score": round(float(score), 4) if score is not None else None,
                "retrieval": {
                    "semantic_distance": result.get("distance"),
                    "keyword_score": result.get("keyword_score"),
                },
            }
            item.update(citation(entry if entry is not None else {
                "id": doc_id,
                "source": metadata.get("source", ""),
                "sdkVersion": metadata.get("sdkVersion", ""),
            }))
            enriched.append(item)
        return enriched
