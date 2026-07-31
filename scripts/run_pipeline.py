"""
Phase 1 完整流水线: SDK 解析 -> 知识构建 -> 依赖图 -> 向量索引
"""

import sys
import os
import time
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdk_parser import SDKParser
from knowledge_builder import KnowledgeBuilder, GraphBuilder
from vector_store import VectorStore
from scripts.sync_rag_docs import sync_docs


def run_pipeline(sdk_path: str = "node_modules/@manycore/idp-sdk/index.d.ts"):
    """运行完整流水线"""

    print("=" * 60)
    print("插件开发 AI Agent - 知识库构建流水线")
    print("=" * 60)
    print()

    # Step 1: AST 解析
    print("[1/5] SDK AST 解析...")
    start = time.time()
    parser = SDKParser(sdk_path)
    symbols = parser.parse()
    parse_time = time.time() - start
    print(f"  ✓ 解析完成: {len(symbols)} 个符号, 耗时 {parse_time:.2f}s")
    print()

    # Step 2: 知识构建
    print("[2/5] SDK 知识库构建...")
    start = time.time()
    kb = KnowledgeBuilder()
    index = kb.build(symbols)
    kb_time = time.time() - start
    print(f"  ✓ 知识库构建完成, 耗时 {kb_time:.2f}s")
    print()

    # Step 3: RAG 文档同步。此处不单独重建索引，统一在最后一次构建。
    print("[3/5] RAG 文档同步...")
    start = time.time()
    rag_changed = sync_docs(rebuild_index=False)
    rag_time = time.time() - start
    print(f"  ✓ RAG 文档同步完成: {'有更新' if rag_changed else '无更新'}, 耗时 {rag_time:.2f}s")
    print()

    # Step 4: 依赖图构建
    print("[4/5] 类型依赖图构建...")
    start = time.time()
    gb = GraphBuilder()
    graph_data = gb.build(symbols)
    graph_time = time.time() - start
    print(f"  ✓ 依赖图构建完成, 耗时 {graph_time:.2f}s")
    print()

    # Step 5: 向量索引。读取统一索引，确保 SDK 和 RAG 文档一并入库。
    print("[5/5] 向量索引构建...")
    start = time.time()
    vs = VectorStore()
    index_path = Path("data/knowledge/_index.json")
    merged_index = json.loads(index_path.read_text(encoding="utf-8"))
    vs.build_index(merged_index)
    vs_time = time.time() - start
    print(f"  ✓ 向量索引构建完成, 耗时 {vs_time:.2f}s")
    print()

    # 汇总
    print("=" * 60)
    print(f"构建完成! 总耗时: {parse_time + kb_time + rag_time + graph_time + vs_time:.2f}s")
    print(f"  - 符号数: {len(symbols)}")
    print(f"  - 知识单元: {len(merged_index)}")
    print(f"  - 向量文档: {vs.count()}")
    print("=" * 60)

    return symbols, merged_index, graph_data


if __name__ == "__main__":
    run_pipeline()
