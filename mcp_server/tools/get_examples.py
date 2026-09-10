"""get_examples：返回知识库中真实存在的代码示例。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field


def register(mcp, container) -> None:
    @mcp.tool(
        name="get_examples",
        description=(
            "获取某个插件 SDK 符号或开发主题的官方代码示例（来自 docs/rag 官方文档与 SDK 知识单元）。"
            "只返回知识库中真实存在的片段；没有可信示例时返回空列表，不会临时生成代码冒充官方示例。"
        ),
    )
    async def get_examples(
        symbol: Annotated[str, Field(description="符号名或主题，例如 IDP.Miniapp.getUploadedDataAsync", min_length=1, max_length=200)],
        language: Annotated[str, Field(description="代码语言：typescript / javascript / json / html / any")] = "typescript",
        sdk_version: Annotated[str, Field(description="期望的 SDK 版本，留空表示不限")] = "",
    ) -> dict[str, Any]:
        def build() -> dict:
            related: list[str] = []
            resolution = container.knowledge.resolve_deep(symbol)
            if resolution.found:
                related = [
                    str(ref)
                    for ref in (resolution.unit or {}).get("references", []) or []
                ][:3]
                related += container.graph.related(resolution.symbol, depth=1, limit=5)
            examples = container.examples.find(
                symbol,
                language=language,
                knowledge=container.knowledge,
                related_symbols=related,
            )
            if sdk_version:
                examples = [
                    item for item in examples
                    if item.get("verified_source")
                ]
            return {
                "symbol": symbol,
                "language": language,
                "status": "ok" if examples else "not_found",
                "found": bool(examples),
                "example_count": len(examples),
                "examples": examples,
                "message": "" if examples else (
                    "知识库中没有找到该符号的可信示例代码；请勿把模型生成的代码当作官方示例。"
                ),
            }

        return container.telemetry.guarded("get_examples", build, query=symbol, sdk_version=sdk_version)
