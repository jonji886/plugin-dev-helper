"""get_related_symbols：基于依赖图展开相关符号。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field


def register(mcp, container) -> None:
    settings = container.settings

    @mcp.tool(
        name="get_related_symbols",
        description=(
            "查询符号的依赖与被引关系，例如某个 API 用到哪些类型、哪些 API 引用了某个类型。"
            "用于构造参数前展开相关类型，或判断改动影响范围。depth 上限为 "
            f"{settings.max_graph_depth}。"
        ),
    )
    async def get_related_symbols(
        symbol: Annotated[str, Field(description="符号名，例如 IDP.Miniapp.uploadDataAsync", min_length=1, max_length=200)],
        depth: Annotated[int, Field(description="展开层数，默认 1", ge=1, le=3)] = 1,
    ) -> dict[str, Any]:
        def build() -> dict:
            safe_depth = max(1, min(int(depth), settings.max_graph_depth))
            resolution = container.knowledge.resolve_deep(symbol)
            resolved = resolution.symbol if resolution.found else symbol

            dependencies = container.graph.dependencies(resolved, safe_depth)
            referenced_by = container.graph.referenced_by(resolved, safe_depth)

            if not dependencies and resolution.found:
                # 依赖图可能缺少该节点，退回知识单元记录的引用关系
                dependencies = [
                    {"symbol": str(ref), "relation": "references", "depth": 1,
                     "type": (container.knowledge.get_entry(str(ref)) or {}).get("type", "")}
                    for ref in (resolution.unit or {}).get("references", []) or []
                ]

            related = []
            seen = set()
            for item in dependencies + referenced_by:
                if item["symbol"] in seen:
                    continue
                seen.add(item["symbol"])
                related.append(item["symbol"])

            return {
                "found": resolution.found or bool(dependencies or referenced_by),
                "status": "ok" if (resolution.found or dependencies or referenced_by) else "not_found",
                "symbol": resolved,
                "depth": safe_depth,
                "dependencies": dependencies,
                "referenced_by": referenced_by,
                "related_symbols": related[: settings.max_graph_nodes],
                "message": "" if related else f"知识库与依赖图中没有找到 `{symbol}` 的关联关系。",
                "results": related[: settings.max_graph_nodes],
            }

        return container.telemetry.guarded("get_related_symbols", build, query=symbol)
