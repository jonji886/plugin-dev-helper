"""
Phase 1 完整流水线: SDK 解析 -> 知识构建 -> 依赖图 -> 向量索引
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdk_parser import SDKParser
from knowledge_builder import KnowledgeBuilder, GraphBuilder
from vector_store import VectorStore


def run_pipeline(sdk_path: str = "node_modules/@manycore/idp-sdk/index.d.ts"):
    """运行完整流水线"""

    print("=" * 60)
    print("插件开发 AI Agent - Phase 1 流水线")
    print("=" * 60)
    print()

    # Step 1: AST 解析
    print("[1/4] SDK AST 解析...")
    start = time.time()
    parser = SDKParser(sdk_path)
    symbols = parser.parse()
    parse_time = time.time() - start
    print(f"  ✓ 解析完成: {len(symbols)} 个符号, 耗时 {parse_time:.2f}s")
    print()

    # Step 2: 知识构建
    print("[2/4] 知识库构建...")
    start = time.time()
    kb = KnowledgeBuilder()
    index = kb.build(symbols)
    kb_time = time.time() - start
    print(f"  ✓ 知识库构建完成, 耗时 {kb_time:.2f}s")
    print()

    # Step 3: 依赖图构建
    print("[3/4] 类型依赖图构建...")
    start = time.time()
    gb = GraphBuilder()
    graph_data = gb.build(symbols)
    graph_time = time.time() - start
    print(f"  ✓ 依赖图构建完成, 耗时 {graph_time:.2f}s")
    print()

    # Step 4: 向量索引
    print("[4/4] 向量索引构建...")
    start = time.time()
    vs = VectorStore()
    vs.build_index(index)
    vs_time = time.time() - start
    print(f"  ✓ 向量索引构建完成, 耗时 {vs_time:.2f}s")
    print()

    # 汇总
    print("=" * 60)
    print(f"Phase 1 完成! 总耗时: {parse_time + kb_time + graph_time + vs_time:.2f}s")
    print(f"  - 符号数: {len(symbols)}")
    print(f"  - 知识单元: {len(index)}")
    print(f"  - 向量文档: {vs.count()}")
    print("=" * 60)

    return symbols, index, graph_data


if __name__ == "__main__":
    run_pipeline()
