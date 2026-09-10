"""MCP Tool 调用埋点：复用既有 SQLite telemetry（app.metrics_store.MetricsStore）。

记录 request_id / tool_name / success / duration_ms / result_count / error_type / timestamp，
不记录 Token、SSH Secret、API Key，也不记录用户完整源码。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

COUNT_KEYS = ("results", "examples", "issues", "candidate_symbols")


class McpTelemetry:
    """轻量 MCP 调用记录器；写入失败不影响 Tool 正常返回。"""

    def __init__(self, database_path: Path | str | None = None, enabled: bool = True):
        self.database_path = Path(database_path) if database_path else None
        self.enabled = enabled and self.database_path is not None
        self._store = None

    @property
    def store(self):
        if self._store is None:
            from app.metrics_store import MetricsStore

            self._store = MetricsStore(self.database_path)
        return self._store

    def record(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self.store.record_mcp_tool(record)
        except Exception as error:  # telemetry 不能拖垮 Tool
            print(f"[mcp][telemetry] record failed: {error}")

    def metrics(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "tools": {}}
        try:
            data = self.store.mcp_metrics()
        except Exception as error:
            return {"enabled": True, "error": str(error), "tools": {}}
        data["enabled"] = True
        return data

    def guarded(self, tool_name: str, builder: Callable[[], dict[str, Any]], **fields: Any) -> dict[str, Any]:
        """执行 Tool 逻辑：异常被收敛为结构化错误响应，不会让 Server 崩溃。"""
        request_id = uuid.uuid4().hex
        started = perf_counter()
        try:
            payload = builder()
            status = "error" if payload.get("status") == "error" else "success"
            error_type = str(payload.get("error_type", "") or "")
        except Exception as error:
            payload = {
                "status": "error",
                "error_type": type(error).__name__,
                "message": f"{tool_name} 执行失败: {error}",
                "results": [],
                "examples": [],
                "issues": [],
            }
            status = "error"
            error_type = type(error).__name__
        duration_ms = round((perf_counter() - started) * 1000, 2)

        result_count = 0
        for key in COUNT_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                result_count = max(result_count, len(value))

        payload.setdefault("status", status)
        payload["request_id"] = request_id
        payload["tool"] = tool_name
        payload["duration_ms"] = duration_ms

        self.record({
            "request_id": request_id,
            "tool_name": tool_name,
            "status": status,
            "error_type": error_type,
            "duration_ms": duration_ms,
            "result_count": result_count,
            "sdk_version": str(fields.get("sdk_version", "") or ""),
            "query": str(fields.get("query", "") or ""),
        })
        return payload
