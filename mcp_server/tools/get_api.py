"""get_api：按符号精确查询 API 定义（走结构化知识索引，不做向量检索）。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp_server.services.common import citation, source_lines

TYPE_KINDS = {"interface", "type_alias", "enum"}


def _signature(name: str, parameters: list[dict]) -> str:
    parts = []
    for parameter in parameters:
        suffix = "?" if parameter.get("optional") else ""
        parts.append(f"{parameter.get('name', '')}{suffix}: {parameter.get('type', '')}")
    return f"{name}({', '.join(parts)})"


def register(mcp, container) -> None:
    @mcp.tool(
        name="get_api",
        description=(
            "按符号名精确查询插件 SDK API 定义，例如 IDP.Miniapp.exit。"
            "返回参数、返回值、源码文件与行号、SDK 版本和相关类型。"
            "精确匹配不存在时返回 candidate_symbols，不会用近似结果冒充精确结果。"
        ),
    )
    async def get_api(
        symbol: Annotated[str, Field(description="完整符号名，例如 IDP.Miniapp.getUploadedDataAsync", min_length=1, max_length=200)],
        sdk_version: Annotated[str, Field(description="期望的 SDK 版本，例如 1.83.0；留空表示不限")] = "",
    ) -> dict[str, Any]:
        def build() -> dict:
            resolution = container.knowledge.resolve_deep(symbol)
            if not resolution.found:
                candidates: list[dict[str, Any]] = []
                if resolution.match_type == "ambiguous":
                    candidates = [
                        container.knowledge.summarize(entry)
                        for entry in resolution.ambiguous[: container.settings.max_candidates]
                    ]
                else:
                    candidates = container.knowledge.candidates(symbol, limit=container.settings.max_candidates)
                return {
                    "found": False,
                    "status": "not_found",
                    "symbol": symbol,
                    "match_type": "none",
                    "sdk_version": sdk_version,
                    "candidate_symbols": candidates,
                    "message": f"知识库中不存在 `{symbol}`，请勿臆造该 API。可参考候选符号或改用 search_docs 检索。",
                    "results": [],
                }

            entry = resolution.entry or {}
            unit = resolution.unit or {}
            is_member_path = resolution.match_type == "path"
            api_name = (
                resolution.symbol.rsplit(".", 1)[-1]
                if is_member_path
                else entry.get("name", "")
            )
            parameters = [
                {
                    "name": parameter.get("name", ""),
                    "type": parameter.get("type", ""),
                    "required": not bool(parameter.get("optional", False)),
                    "description": parameter.get("description", ""),
                }
                for parameter in unit.get("parameters", []) or []
            ]
            references = [str(ref) for ref in (unit.get("references", []) or [])]
            related_types = [
                ref for ref in references
                if (container.knowledge.get_entry(ref) or {}).get("type") in TYPE_KINDS
            ]
            payload: dict[str, Any] = {
                "found": True,
                "status": "ok",
                "symbol": resolution.symbol,
                "name": api_name,
                "namespace": entry.get("namespace", ""),
                "kind": unit.get("type") or entry.get("type", ""),
                "match_type": resolution.match_type,
                "description": entry.get("description", ""),
                "signature": _signature(api_name, unit.get("parameters", []) or []),
                "parameters": parameters,
                "return_type": unit.get("returnType"),
                "methods": unit.get("methods", []) or [],
                "aliases": entry.get("aliases", []) or [],
                "related_types": related_types,
                "references": references,
                "sdk_version": entry.get("sdkVersion", ""),
            }
            payload.update(citation(entry))
            payload["source_lines"] = source_lines(entry)
            if is_member_path:
                # symbol / name 必须保持调用方请求的路径，不能被所属类型覆盖
                payload["symbol"] = resolution.symbol
                payload["name"] = api_name
                payload["definition_owner"] = entry.get("id", "")
                payload["source_note"] = (
                    "成员级行号未在知识索引中单独记录，source_lines 指向其所属类型。"
                )
            if sdk_version and payload["sdk_version"] and payload["sdk_version"] != sdk_version:
                payload["version_warning"] = (
                    f"该符号记录于 SDK v{payload['sdk_version']}，与指定的 v{sdk_version} 不一致。"
                )
            return payload

        return container.telemetry.guarded("get_api", build, query=symbol, sdk_version=sdk_version)
