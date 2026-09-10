"""search_docs：自然语言插件开发问题检索（复用 Hybrid Retrieval）。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp_server.services.common import truncate


def register(mcp, container) -> None:
    settings = container.settings

    @mcp.tool(
        name="search_docs",
        description=(
            "用自然语言检索插件 SDK/API/开发文档。适合「工具插件 UI 和 VM 怎么通信」"
            "「如何获取方案 JSON」这类问题。返回带 source/source_lines/sdk_version 的"
            "结构化片段，不生成总结。需要精确查某个 API 定义时请改用 get_api。"
        ),
    )
    async def search_docs(
        query: Annotated[str, Field(description="自然语言问题或关键词", min_length=1, max_length=500)],
        top_k: Annotated[int, Field(description="返回结果数量", ge=1, le=20)] = 5,
        sdk_version: Annotated[str, Field(description="限定 SDK 版本，例如 1.83.0；留空表示不限")] = "",
    ) -> dict[str, Any]:
        def build() -> dict:
            limit = max(1, min(int(top_k), settings.max_search_top_k))
            used_top_k = min(limit, settings.max_search_top_k)
            mode = "hybrid"
            results: list[dict] = []
            if container.retrieval.is_available():
                raw = container.retrieval.search(query, top_k=used_top_k)
                results = container.retrieval.enrich(raw, container.knowledge, settings.max_search_content_chars)
            else:
                # 向量库不可用时的降级：只做知识索引词法匹配，并明确标记 mode
                mode = "keyword_fallback"
                for item in container.knowledge.candidates(query, limit=used_top_k):
                    entry = container.knowledge.get_entry(item["symbol"])
                    if entry is None:
                        continue
                    from mcp_server.services.common import citation

                    payload = dict(item)
                    payload.update(citation(entry))
                    payload["title"] = entry.get("name", item["symbol"])
                    payload["content"] = truncate(container.knowledge.markdown(entry), settings.max_search_content_chars)
                    payload["retrieval"] = {"semantic_distance": None, "keyword_score": item.get("score")}
                    results.append(payload)

            if sdk_version:
                results = [
                    item for item in results
                    if not item.get("sdk_version") or item.get("sdk_version") == sdk_version
                    or item.get("type") == "document"
                ]

            status = "ok" if results else "not_found"
            return {
                "query": query,
                "sdk_version": sdk_version,
                "mode": mode,
                "status": status,
                "result_count": len(results),
                "results": results,
                "message": "" if results else "没有检索到可信证据，请换用更具体的关键词或改用 get_api 精确查询。",
            }

        return container.telemetry.guarded("search_docs", build, query=query, sdk_version=sdk_version)
