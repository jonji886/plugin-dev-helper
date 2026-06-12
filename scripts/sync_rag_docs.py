"""
同步 docs/rag 下的文档到现有知识库格式。

将 `docs/rag/**/*.md` 转换为 `data/knowledge/*.md`、`data/knowledge/*.json`，并更新
`data/knowledge/_index.json`。当内容有变化时，调用现有向量库重建索引。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vector_store import VectorStore

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs" / "rag"
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
INDEX_PATH = KNOWLEDGE_DIR / "_index.json"


@dataclass
class RagDocEntry:
    """RAG 文档入库条目。"""

    id: str
    name: str
    type: str
    namespace: str
    description: str
    aliases: list[str]
    source: str
    sdkVersion: str
    mdFile: str
    jsonFile: str
    references: list[str]
    startLine: int
    endLine: int
    sourcePath: str
    contentHash: str
    is_overview: bool = False  # 是否为总览型文档


def slugify_path(path: Path) -> str:
    """将相对路径转换为稳定的知识库 id。"""
    parts = list(path.with_suffix("").parts)
    if parts and parts[0].isdigit():
        parts = parts[1:]
    safe_parts = [re.sub(r"[^a-zA-Z0-9._-]+", "_", part).strip("._-") for part in parts]
    safe_parts = [part for part in safe_parts if part]
    if not safe_parts:
        safe_parts = [path.stem]
    return "rag." + ".".join(safe_parts)


def safe_filename(value: str) -> str:
    """生成适合落盘的文件名。"""
    return value.replace(".", "_").replace("/", "_")


def extract_title(content: str, fallback: str) -> str:
    """从 markdown 中提取标题。"""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return fallback


def extract_description(content: str) -> str:
    """提取首段作为摘要。"""
    lines = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            if lines:
                break
            continue
        if line.startswith("#"):
            continue
        if line.startswith(">"):
            continue
        lines.append(line)
    return " ".join(lines[:3])[:240]


def build_markdown(title: str, source_path: str, content: str) -> str:
    """生成入库用 markdown。"""
    return f"# {title}\n\n- **类型**: document\n- **来源**: {source_path}\n\n{content.strip()}\n"


def hash_text(text: str) -> str:
    """计算内容哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_index() -> list[dict]:
    """读取已有索引。"""
    if not INDEX_PATH.exists():
        return []
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    """写入 JSON 文件。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_docs() -> Iterable[Path]:
    """遍历 docs/rag 下的 markdown 文档。"""
    if not DOCS_DIR.exists():
        return []
    return sorted(DOCS_DIR.rglob("*.md"))


def build_entry(doc_path: Path) -> tuple[RagDocEntry, str]:
    """将源文档转换为可入库条目。"""
    content = doc_path.read_text(encoding="utf-8")
    rel_path = doc_path.relative_to(DOCS_DIR)
    doc_id = slugify_path(rel_path)
    title = extract_title(content, doc_path.stem)
    description = extract_description(content)
    md_content = build_markdown(title, f"docs/rag/{rel_path.as_posix()}", content)
    content_hash = hash_text(md_content)
    safe_id = safe_filename(doc_id)

    # 判断是否为总览型文档（通过文件名关键词识别）
    overview_keywords = ["说明", "介绍", "概述", "概览", "overview", "guide", "intro"]
    is_overview = any(kw in doc_path.stem.lower() for kw in overview_keywords)

    entry = RagDocEntry(
        id=doc_id,
        name=title,
        type="document",
        namespace="docs.rag",
        description=description,
        aliases=[title, doc_path.stem],
        source=f"docs/rag/{rel_path.as_posix()}",
        sdkVersion="",
        mdFile=f"{safe_id}.md",
        jsonFile=f"{safe_id}.json",
        references=[],
        startLine=1,
        endLine=max(1, content.count("\n") + 1),
        sourcePath=str(rel_path).replace("\\", "/"),
        contentHash=content_hash,
        is_overview=is_overview,
    )
    return entry, md_content


def sync_docs(rebuild_index: bool = True) -> bool:
    """同步 docs/rag 文档到知识库并在必要时重建索引。"""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    previous_index = load_index()
    previous_by_source = {
        item.get("sourcePath") or item.get("source", "").replace("docs/rag/", ""): item
        for item in previous_index
        if item.get("namespace") == "docs.rag"
    }

    previous_ids = {item.get("id") for item in previous_index if item.get("namespace") == "docs.rag"}

    new_index: list[dict] = []
    changed = False
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()

    for doc_path in iter_docs():
        entry, md_content = build_entry(doc_path)
        if entry.id in seen_ids:
            raise ValueError(f"Duplicate RAG doc id detected: {entry.id}")
        seen_ids.add(entry.id)
        seen_sources.add(entry.sourcePath)

        prev = previous_by_source.get(entry.sourcePath)
        if entry.id in previous_ids:
            changed = True
        elif not prev or prev.get("contentHash") != entry.contentHash:
            changed = True

        md_path = KNOWLEDGE_DIR / entry.mdFile
        json_path = KNOWLEDGE_DIR / entry.jsonFile
        md_path.write_text(md_content, encoding="utf-8")
        write_json(json_path, asdict(entry))
        new_index.append({
            "id": entry.id,
            "name": entry.name,
            "type": entry.type,
            "namespace": entry.namespace,
            "description": entry.description,
            "aliases": entry.aliases,
            "source": entry.source,
            "sdkVersion": entry.sdkVersion,
            "mdFile": entry.mdFile,
            "jsonFile": entry.jsonFile,
            "references": entry.references,
            "startLine": entry.startLine,
            "endLine": entry.endLine,
            "sourcePath": entry.sourcePath,
            "contentHash": entry.contentHash,
            "is_overview": entry.is_overview,
        })

    removed_sources = set(previous_by_source) - seen_sources
    if removed_sources:
        changed = True
        for source in removed_sources:
            prev = previous_by_source[source]
            for key in (prev.get("mdFile"), prev.get("jsonFile")):
                if key:
                    target = KNOWLEDGE_DIR / key
                    if target.exists():
                        target.unlink()

    # 保留非 docs/rag 的既有条目
    retained_index = [item for item in previous_index if item.get("namespace") != "docs.rag"]
    merged_index = retained_index + new_index
    INDEX_PATH.write_text(json.dumps(merged_index, ensure_ascii=False, indent=2), encoding="utf-8")

    if changed and rebuild_index:
        vs = VectorStore()
        vs.build_index(merged_index, knowledge_dir=str(KNOWLEDGE_DIR))

    return changed


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="同步 docs/rag 下的 markdown 文档到知识库")
    parser.add_argument("--no-reindex", action="store_true", help="仅同步知识文件，不重建向量索引")
    args = parser.parse_args()

    updated = sync_docs(rebuild_index=not args.no_reindex)
    print("同步完成，索引已更新" if updated else "同步完成，无需重建索引")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
