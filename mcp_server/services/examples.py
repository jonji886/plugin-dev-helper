"""代码示例服务：只返回知识库中真实存在的代码片段。

来源优先级：
1. 符号自身的知识单元 Markdown（SDK AST 产物）
2. docs/rag 官方文档（sync_rag_docs 同步产物）
3. 依赖图一跳范围内的相关符号 Markdown

不通过 LLM 生成示例；没有可信示例时返回空列表。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp_server.services.common import truncate

FENCE_PATTERN = re.compile(r"```([A-Za-z0-9_+-]*)\r?\n(.*?)```", re.DOTALL)

JS_FAMILY = {"ts", "tsx", "typescript", "js", "jsx", "javascript", "mjs", "cjs"}
LANGUAGE_ALIASES = {
    "typescript": JS_FAMILY,
    "ts": JS_FAMILY,
    "tsx": JS_FAMILY,
    "javascript": JS_FAMILY,
    "js": JS_FAMILY,
    "json": {"json"},
    "html": {"html"},
}


def _normalized_language(raw: str) -> str:
    return (raw or "").strip().lower()


def language_matches(requested: str, block_language: str) -> bool:
    if not requested or requested == "any":
        return True
    allowed = LANGUAGE_ALIASES.get(requested.lower())
    if allowed is None:
        return _normalized_language(block_language) == requested.lower()
    return _normalized_language(block_language) in allowed or not block_language


class ExampleService:
    """从真实文档/知识单元中提取代码示例。"""

    def __init__(self, knowledge_dir: str | Path, rag_docs_path: str | Path,
                 max_examples: int = 5, max_example_chars: int = 4000):
        self.knowledge_dir = Path(knowledge_dir)
        self.rag_docs_path = Path(rag_docs_path)
        self.max_examples = max_examples
        self.max_example_chars = max_example_chars

    def _rag_documents(self) -> list[tuple[str, str]]:
        if not self.rag_docs_path.exists():
            return []
        return [
            (f"docs/rag/{path.name}", path.read_text(encoding="utf-8"))
            for path in sorted(self.rag_docs_path.glob("*.md"))
        ]

    @staticmethod
    def _blocks(text: str) -> list[dict[str, str]]:
        return [
            {"language": _normalized_language(match.group(1)), "code": match.group(2).rstrip()}
            for match in FENCE_PATTERN.finditer(text)
        ]

    def find(self, symbol: str, language: str = "typescript", knowledge=None,
             related_symbols: list[str] | None = None) -> list[dict[str, Any]]:
        """按符号查找真实代码片段；`knowledge` 为 KnowledgeService，可为空。"""
        resolved_symbol = (symbol or "").strip()
        short_name = resolved_symbol.rsplit(".", 1)[-1]
        needles = {resolved_symbol, short_name}
        if resolved_symbol.startswith("IDP."):
            needles.add(resolved_symbol[len("IDP."):])
        needles = {needle for needle in needles if needle and len(needle) > 1}

        scope_files: list[tuple[str, str, bool, str]] = []
        # (source, text, verified, owner_symbol)
        if knowledge is not None:
            for candidate in [resolved_symbol, *(related_symbols or [])]:
                resolution = knowledge.resolve(str(candidate))
                if not resolution.found or not resolution.entry:
                    continue
                markdown = knowledge.markdown(resolution.entry)
                if markdown:
                    scope_files.append((
                        f"knowledge/{resolution.entry.get('mdFile', '')}",
                        markdown,
                        True,
                        str(resolution.entry.get("id", "")),
                    ))
        for name, text in self._rag_documents():
            scope_files.append((name, text, True, ""))

        examples: list[dict[str, Any]] = []
        for source, text, verified, owner in scope_files:
            is_symbol_doc = bool(owner) and owner == resolved_symbol
            doc_mentions = any(needle in text for needle in needles) if needles else False
            if not is_symbol_doc and not doc_mentions:
                continue
            for block in self._blocks(text):
                if not language_matches(language, block["language"]):
                    continue
                code = block["code"]
                if not code.strip():
                    continue
                direct = any(needle in code for needle in needles) if needles else False
                if not direct and not (is_symbol_doc or doc_mentions):
                    continue
                examples.append({
                    "code": truncate(code, self.max_example_chars),
                    "language": block["language"] or language,
                    "source": source,
                    "verified_source": bool(verified and (source.startswith("docs/rag/") or is_symbol_doc)),
                    "match": "direct" if direct else "document",
                })
                if len(examples) >= self.max_examples:
                    return examples
        return examples
