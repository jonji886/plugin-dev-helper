"""MCP 返回结构的公共装配工具。

每个结果都尽量带 source / symbol / sdk_version / source_file / source_lines，
全部来自真实知识索引，不由模型生成。
"""

from __future__ import annotations

from typing import Any


def source_lines(entry: dict | None) -> dict[str, int]:
    entry = entry or {}
    start = entry.get("startLine", 0) or 0
    end = entry.get("endLine", 0) or 0
    return {"start": int(start), "end": int(end)}


def location(entry: dict | None) -> str:
    entry = entry or {}
    lines = source_lines(entry)
    source = entry.get("source", "") or ""
    if not source:
        return ""
    if lines["start"] and lines["end"] and lines["start"] != lines["end"]:
        return f"{source}:{lines['start']}-{lines['end']}"
    if lines["start"]:
        return f"{source}:{lines['start']}"
    return source


def citation(entry: dict | None) -> dict[str, Any]:
    entry = entry or {}
    return {
        "symbol": entry.get("id", ""),
        "source": entry.get("source", ""),
        "sdk_version": entry.get("sdkVersion", ""),
        "source_file": entry.get("source", ""),
        "source_lines": source_lines(entry),
        "location": location(entry),
    }


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
