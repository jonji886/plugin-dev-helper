"""get_type：按名称查询 interface / type / enum 定义。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp_server.services.common import citation, source_lines


def register(mcp, container) -> None:
    @mcp.tool(
        name="get_type",
        description=(
            "查询插件 SDK 类型定义（interface / type_alias / enum）的字段、"
            "是否必填、枚举值和依赖类型，例如 MiniappUploadDataOption。"
            "构造 API 入参前优先调用这个工具。"
        ),
    )
    async def get_type(
        name: Annotated[str, Field(description="类型名，例如 MiniappUploadDataOption", min_length=1, max_length=200)],
        sdk_version: Annotated[str, Field(description="期望的 SDK 版本，留空表示不限")] = "",
    ) -> dict[str, Any]:
        def build() -> dict:
            resolution = container.knowledge.resolve(name)
            if not resolution.found:
                candidates = (
                    [container.knowledge.summarize(entry) for entry in resolution.ambiguous[: container.settings.max_candidates]]
                    if resolution.match_type == "ambiguous"
                    else container.knowledge.candidates(name, limit=container.settings.max_candidates)
                )
                return {
                    "found": False,
                    "status": "not_found",
                    "name": name,
                    "match_type": "none",
                    "candidate_symbols": candidates,
                    "message": f"知识库中不存在类型 `{name}`，请勿臆造字段。",
                    "results": [],
                }

            entry = resolution.entry or {}
            unit = resolution.unit or {}
            fields = [
                {
                    "name": prop.get("name", ""),
                    "type": prop.get("type", ""),
                    "required": not bool(prop.get("optional", False)),
                    "readonly": bool(prop.get("readonly", False)),
                    "description": prop.get("description", ""),
                }
                for prop in unit.get("properties", []) or []
            ]
            payload: dict[str, Any] = {
                "found": True,
                "status": "ok",
                "name": resolution.symbol,
                "kind": entry.get("type", ""),
                "match_type": resolution.match_type,
                "description": entry.get("description", ""),
                "fields": fields,
                "methods": unit.get("methods", []) or [],
                "enum_members": unit.get("enumMembers", []) or [],
                "type_parameters": unit.get("typeParameters", []) or [],
                "dependencies": [str(ref) for ref in (unit.get("references", []) or [])],
                "sdk_version": entry.get("sdkVersion", ""),
            }
            payload.update(citation(entry))
            payload["source_lines"] = source_lines(entry)
            return payload

        return container.telemetry.guarded("get_type", build, query=name, sdk_version=sdk_version)
