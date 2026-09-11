"""依赖图服务：复用 data/graph/dependency_graph.json（GraphBuilder 产物）。

提供 dependencies / referenced_by / related_symbols 三类查询，
并对 depth 和节点数量做上限保护，避免图无限展开。
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


class GraphService:
    """只读依赖图服务。"""

    def __init__(self, graph_path: str | Path, max_depth: int = 3, max_nodes: int = 50):
        self.graph_path = Path(graph_path)
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self._graph: dict | None = None
        self._out: dict[str, list[dict]] = {}
        self._in: dict[str, list[dict]] = {}
        self._node_types: dict[str, str] = {}

    def graph(self) -> dict:
        if self._graph is None:
            try:
                data = json.loads(self.graph_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                data = {"nodes": [], "edges": []}
            self._graph = data
            self._build_adjacency(data)
        return self._graph

    def _build_adjacency(self, data: dict) -> None:
        self._out = {}
        self._in = {}
        self._node_types = {}
        for node in data.get("nodes", []) or []:
            node_id = node.get("id") if isinstance(node, dict) else node
            if not node_id:
                continue
            if isinstance(node, dict):
                self._node_types[str(node_id)] = str(node.get("type", "") or "")
        for edge in data.get("edges", []) or []:
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            if not source or not target:
                continue
            self._out.setdefault(source, []).append(edge)
            self._in.setdefault(target, []).append(edge)

    def reload(self) -> None:
        self._graph = None
        self.graph()

    def is_available(self) -> bool:
        return bool(self.graph().get("nodes"))

    def has_symbol(self, symbol: str) -> bool:
        self.graph()
        return symbol in self._node_types

    def _bfs(self, symbol: str, adjacency: dict[str, list[dict]], depth: int,
             direction: str) -> list[dict]:
        depth = max(1, min(int(depth), self.max_depth))
        visited: set[str] = set()
        results: list[dict] = []
        queue: deque[tuple[str, int]] = deque([(symbol, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for edge in adjacency.get(current, []):
                other = str(edge.get("target" if direction == "out" else "source", ""))
                if not other or other == symbol or other in visited:
                    continue
                visited.add(other)
                if len(visited) > self.max_nodes:
                    break
                results.append({
                    "symbol": other,
                    "relation": edge.get("relation", "references"),
                    "depth": level + 1,
                    "type": self._node_types.get(other, ""),
                })
                queue.append((other, level + 1))
            if len(visited) > self.max_nodes:
                break
        return results

    def dependencies(self, symbol: str, depth: int = 1) -> list[dict]:
        """该符号引用了哪些符号（出边）。"""
        return self._bfs(symbol, self._out, depth, "out")

    def referenced_by(self, symbol: str, depth: int = 1) -> list[dict]:
        """哪些符号引用了该符号（入边）。"""
        return self._bfs(symbol, self._in, depth, "in")

    def related(self, symbol: str, depth: int = 1, limit: int = 20) -> list[str]:
        combined = self.dependencies(symbol, depth) + self.referenced_by(symbol, depth)
        ordered = sorted(combined, key=lambda item: (item["depth"], item["symbol"]))
        seen: set[str] = set()
        symbols: list[str] = []
        for item in ordered:
            if item["symbol"] in seen:
                continue
            seen.add(item["symbol"])
            symbols.append(item["symbol"])
            if len(symbols) >= limit:
                break
        return symbols

    def stats(self) -> dict[str, Any]:
        data = self.graph()
        return {
            "nodes": len(self._node_types),
            "edges": len(data.get("edges", []) or []),
            "max_depth": self.max_depth,
            "max_nodes": self.max_nodes,
        }
