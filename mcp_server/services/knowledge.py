"""知识索引服务：基于 data/knowledge 的结构化精确查询。

复用既有 Knowledge Builder 产物：
- `_index.json`：知识单元索引（id / type / namespace / source / sdkVersion / 行号 / 别名）
- `<symbol>.json`：AST 解析出的结构化元数据（参数、属性、方法、枚举值、引用）

该服务只做结构化查询，不做向量检索，也不生成任何内容。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TYPE_KINDS = {"interface", "type_alias", "enum", "class"}
CONTAINER_KINDS = {"const", "variable", "interface", "class"}


@dataclass
class MemberRef:
    """通过类型成员解析出的节点（不在索引中的方法/属性）。"""

    kind: str  # method / property
    definition: dict
    type_entry: dict | None = None


@dataclass
class PathResolution:
    """沿 `IDP.Miniapp.view.defaultFrame.postMessage` 这类路径逐级解析的结果。"""

    found: bool
    symbol: str
    kind: str = "symbol"  # symbol / method / property
    entry: dict | None = None
    type_entry: dict | None = None
    member: MemberRef | None = None
    owner_entry: dict | None = None
    confirmed_prefix: str = ""
    failed_at: str = ""
    uncertain: bool = False


@dataclass
class SymbolResolution:
    """符号解析结果。"""

    found: bool
    symbol: str
    match_type: str = "none"  # exact / alias / case_insensitive / suffix / none
    entry: dict | None = None
    unit: dict | None = None
    ambiguous: list[dict] = field(default_factory=list)


class KnowledgeService:
    """只读知识索引服务。"""

    def __init__(self, knowledge_dir: str | Path):
        self.knowledge_dir = Path(knowledge_dir)
        self._index: list[dict] | None = None
        self._by_id: dict[str, dict] = {}
        self._by_alias: dict[str, list[dict]] = {}
        self._by_lower_id: dict[str, list[dict]] = {}
        self._unit_cache: dict[str, dict] = {}
        self._prefix_cache: set[str] | None = None

    # ---------- 索引加载 ----------

    @property
    def index_path(self) -> Path:
        return self.knowledge_dir / "_index.json"

    def index(self) -> list[dict]:
        if self._index is None:
            try:
                entries = json.loads(self.index_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                entries = []
            self._index = [entry for entry in entries if isinstance(entry, dict)]
            self._build_lookups()
        return self._index

    def _build_lookups(self) -> None:
        self._by_id = {}
        self._by_alias = {}
        self._by_lower_id = {}
        for entry in self._index or []:
            symbol_id = str(entry.get("id", ""))
            if not symbol_id:
                continue
            self._by_id[symbol_id] = entry
            self._by_lower_id.setdefault(symbol_id.lower(), []).append(entry)
            for alias in entry.get("aliases", []) or []:
                if alias:
                    self._by_alias.setdefault(str(alias), []).append(entry)

    def reload(self) -> None:
        """重新读取索引（知识库重建后调用）。"""
        self._index = None
        self._unit_cache = {}
        self._prefix_cache = None
        self.index()

    @property
    def size(self) -> int:
        return len(self.index())

    def is_available(self) -> bool:
        return bool(self.index())

    # ---------- 查询 ----------

    def get_entry(self, symbol: str) -> dict | None:
        return self._by_id.get(symbol)

    def load_unit(self, entry: dict) -> dict:
        """读取知识单元的结构化元数据（AST 产物）。"""
        symbol_id = str(entry.get("id", ""))
        if symbol_id in self._unit_cache:
            return self._unit_cache[symbol_id]
        unit: dict[str, Any] = {}
        json_file = entry.get("jsonFile")
        if json_file:
            path = self.knowledge_dir / str(json_file)
            try:
                unit = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                unit = {}
        if not unit:
            unit = dict(entry)
        self._unit_cache[symbol_id] = unit
        return unit

    def markdown(self, entry: dict, limit: int = 0) -> str:
        md_file = entry.get("mdFile")
        if not md_file:
            return ""
        path = self.knowledge_dir / str(md_file)
        try:
            content = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return ""
        return content[:limit] if limit and len(content) > limit else content

    def resolve(self, symbol: str) -> SymbolResolution:
        """精确优先解析符号；无法精确命中时不冒充精确结果。"""
        query = (symbol or "").strip()
        if not query:
            return SymbolResolution(found=False, symbol=symbol or "", match_type="none")
        self.index()

        if query in self._by_id:
            return self._finish(query, self._by_id[query], "exact")

        alias_hits = self._by_alias.get(query, [])
        if len(alias_hits) == 1:
            return self._finish(query, alias_hits[0], "alias")
        if len(alias_hits) > 1:
            return SymbolResolution(
                found=False,
                symbol=query,
                match_type="ambiguous",
                ambiguous=alias_hits[:10],
            )

        lowered = query.lower()
        lowered_hits = self._by_lower_id.get(lowered, [])
        if len(lowered_hits) == 1:
            return self._finish(query, lowered_hits[0], "case_insensitive")

        suffix_hits = [
            entry
            for entry in self._index or []
            if str(entry.get("id", "")).lower().endswith("." + lowered)
        ]
        if len(suffix_hits) == 1:
            return self._finish(query, suffix_hits[0], "suffix")

        return SymbolResolution(found=False, symbol=query, match_type="none")

    def _finish(self, query: str, entry: dict, match_type: str) -> SymbolResolution:
        return SymbolResolution(
            found=True,
            symbol=str(entry.get("id", query)),
            match_type=match_type,
            entry=entry,
            unit=self.load_unit(entry),
        )

    # ---------- 路径解析（namespace / const / interface 成员） ----------

    def resolve_deep(self, symbol: str) -> SymbolResolution:
        """先做符号级精确匹配，失败时再沿成员路径解析。"""
        resolution = self.resolve(symbol)
        if resolution.found:
            return resolution
        path = self.resolve_path(symbol)
        if not path.found:
            return resolution
        entry = path.owner_entry or path.type_entry or path.entry
        unit: dict = {}
        if path.member is not None:
            definition = path.member.definition or {}
            unit = {
                "id": path.symbol,
                "name": path.symbol.split(".")[-1],
                "type": path.member.kind,
                "description": definition.get("description", ""),
                "parameters": [
                    {"name": p.get("name", ""), "type": p.get("type", ""),
                     "optional": False, "description": ""}
                    for p in definition.get("parameters", []) or []
                ],
                "returnType": definition.get("return_type") or definition.get("returnType"),
                "value": definition.get("value", ""),
                "enumMembers": [definition] if path.member.kind == "enum_member" else [],
            }
        elif entry is not None:
            unit = self.load_unit(entry)
        return SymbolResolution(
            found=True,
            symbol=path.symbol,
            match_type="path",
            entry=entry,
            unit=unit,
        )

    @staticmethod
    def _clean_type_name(type_name: str) -> str:
        name = (type_name or "").strip()
        for token in ("<", "[", "(", "|"):
            if token in name:
                name = name.split(token)[0]
        return name.strip().rstrip(".")

    def _resolve_named_type(self, type_name: str) -> dict | None:
        name = self._clean_type_name(type_name)
        if not name:
            return None
        resolved = self.resolve(name)
        if resolved.found and str((resolved.entry or {}).get("type", "")) in TYPE_KINDS:
            return resolved.entry
        return None

    def _type_container(self, entry: dict | None) -> dict | None:
        """返回符号可用于成员查找的类型条目（const 会解析到其引用类型）。"""
        if entry is None:
            return None
        kind = str(entry.get("type", ""))
        if kind in TYPE_KINDS:
            return entry
        if kind not in CONTAINER_KINDS:
            return None
        for ref in self.load_unit(entry).get("references", []) or []:
            candidate = self.get_entry(str(ref))
            if candidate is not None and str(candidate.get("type", "")) in TYPE_KINDS:
                return candidate
        return None

    def _member(self, entry: dict | None, type_entry: dict | None, name: str) -> MemberRef | None:
        containers = [type_entry]
        if entry is not None and str(entry.get("type", "")) in TYPE_KINDS:
            containers.append(entry)
        for container in containers:
            if container is None:
                continue
            unit = self.load_unit(container)
            for method in unit.get("methods", []) or []:
                if method.get("name") == name:
                    return MemberRef(
                        kind="method",
                        definition=method,
                        type_entry=self._resolve_named_type(method.get("returnType", "")),
                    )
            for prop in unit.get("properties", []) or []:
                if prop.get("name") == name:
                    return MemberRef(
                        kind="property",
                        definition=prop,
                        type_entry=self._resolve_named_type(prop.get("type", "")),
                    )
            for member in unit.get("enumMembers", []) or []:
                if member.get("name") == name:
                    return MemberRef(kind="enum_member", definition=member)
        return None

    def resolve_path(self, symbol: str) -> PathResolution:
        """逐级解析 `IDP.X.y.z`；成员定义在类型里但不在索引中时也能命中。"""
        self.index()
        parts = (symbol or "").strip().split(".")
        if len(parts) < 2 or parts[0] not in self.known_prefixes():
            return PathResolution(found=False, symbol=symbol or "", confirmed_prefix=parts[0] if parts else "")

        node_id = parts[0]
        entry: dict | None = None
        type_entry: dict | None = None
        last_member: MemberRef | None = None
        owner_entry: dict | None = None

        for segment in parts[1:]:
            candidate_id = f"{node_id}.{segment}"
            candidate = self.get_entry(candidate_id)
            if candidate is not None:
                entry = candidate
                type_entry = self._type_container(candidate)
                last_member = None
                node_id = candidate_id
                continue
            if candidate_id in self.known_prefixes():
                # 纯命名空间（例如 IDP.Miniapp），成员以独立符号存在于索引中
                entry = None
                type_entry = None
                last_member = None
                node_id = candidate_id
                continue
            member = self._member(entry, type_entry, segment)
            if member is None:
                return PathResolution(
                    found=False,
                    symbol=symbol,
                    confirmed_prefix=node_id,
                    failed_at=segment,
                    uncertain=type_entry is not None,
                )
            last_member = member
            owner_entry = type_entry or entry
            entry = None
            type_entry = member.type_entry
            node_id = candidate_id

        if last_member is not None:
            return PathResolution(
                found=True,
                symbol=node_id,
                kind=last_member.kind,
                type_entry=type_entry,
                member=last_member,
                owner_entry=owner_entry,
                confirmed_prefix=node_id,
            )
        return PathResolution(
            found=True, symbol=node_id, kind=str((entry or {}).get("type", "symbol")),
            entry=entry, type_entry=type_entry, confirmed_prefix=node_id,
        )

    def known_prefixes(self) -> set[str]:
        if self._prefix_cache is None:
            prefixes: set[str] = set()
            for entry in self.index():
                symbol_id = str(entry.get("id", ""))
                if not symbol_id:
                    continue
                parts = symbol_id.split(".")
                for size in range(1, len(parts) + 1):
                    prefixes.add(".".join(parts[:size]))
            self._prefix_cache = prefixes
        return self._prefix_cache

    # ---------- 候选与命名空间 ----------

    def candidates(self, symbol: str, limit: int = 5) -> list[dict]:
        """返回近似候选符号，供上层区分「精确不存在」与「可能想查别的」。"""
        query = (symbol or "").strip().lower()
        if not query:
            return []
        tail = query.rsplit(".", 1)[-1]
        scored: list[tuple[float, dict]] = []
        for entry in self.index():
            symbol_id = str(entry.get("id", ""))
            lowered = symbol_id.lower()
            name = str(entry.get("name", "")).lower()
            score = 0.0
            if tail and tail == name:
                score = max(score, 8.0)
            elif tail and tail in lowered:
                score = max(score, 6.0)
            elif query in lowered or lowered.endswith(query):
                score = max(score, 5.0)
            elif tail and name.startswith(tail[:3]) and len(tail) >= 3:
                score = max(score, 3.0)
            elif query and self._token_overlap(query, lowered):
                score = max(score, 2.0)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], len(str(item[1].get("id", "")))))
        return [self.summarize(entry, score) for score, entry in scored[:limit]]

    @staticmethod
    def _token_overlap(query: str, candidate: str) -> bool:
        query_tokens = {token for token in query.replace(".", " ").split() if len(token) > 2}
        return any(token in candidate for token in query_tokens)

    def namespace_members(self, namespace: str, limit: int = 20) -> list[str]:
        prefix = f"{namespace}." if namespace else ""
        members = [
            str(entry.get("id", ""))
            for entry in self.index()
            if str(entry.get("id", "")).startswith(prefix)
            and str(entry.get("id", "")).count(".") == prefix.count(".")
        ]
        return sorted(members)[:limit]

    def sdk_versions(self) -> list[str]:
        versions = {
            str(entry.get("sdkVersion", "")).strip()
            for entry in self.index()
            if str(entry.get("sdkVersion", "")).strip()
        }
        return sorted(versions)

    def symbols_for_version(self, sdk_version: str) -> set[str]:
        return {
            str(entry.get("id", ""))
            for entry in self.index()
            if str(entry.get("sdkVersion", "")) == sdk_version
        }

    @staticmethod
    def summarize(entry: dict, score: float | None = None) -> dict:
        summary = {
            "symbol": entry.get("id", ""),
            "type": entry.get("type", ""),
            "namespace": entry.get("namespace", ""),
            "description": entry.get("description", ""),
            "sdk_version": entry.get("sdkVersion", ""),
            "source_file": entry.get("source", ""),
        }
        if score is not None:
            summary["score"] = round(float(score), 3)
        return summary
