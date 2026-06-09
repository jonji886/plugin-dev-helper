"""
知识构建器

接收解析后的 Symbol 列表，生成：
- 每个 Symbol 的 Markdown 文档
- metadata JSON 文件
- 知识库索引
"""

import json
import os
from pathlib import Path
from typing import Optional

from sdk_parser.models import Symbol


class KnowledgeBuilder:
    """知识构建器"""

    def __init__(self, output_dir: str = "data/knowledge"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build(self, symbols: list[Symbol]) -> list[dict]:
        """构建知识库，返回所有知识单元的索引"""
        index = []

        for symbol in symbols:
            # 生成 Markdown
            md_content = symbol.to_markdown()

            # 生成 metadata
            metadata = symbol.to_dict()

            # 保存 Markdown 文件
            safe_id = symbol.id.replace(".", "_").replace("/", "_")
            md_path = self.output_dir / f"{safe_id}.md"
            md_path.write_text(md_content, encoding="utf-8")

            # 保存 metadata JSON
            json_path = self.output_dir / f"{safe_id}.json"
            json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            # 添加到索引
            index_entry = {
                "id": symbol.id,
                "name": symbol.name,
                "type": symbol.symbol_type,
                "namespace": ".".join(symbol.namespace_path),
                "description": symbol.description[:200] if symbol.description else "",
                "aliases": symbol.aliases,
                "source": symbol.source,
                "sdkVersion": symbol.sdk_version,
                "mdFile": f"{safe_id}.md",
                "jsonFile": f"{safe_id}.json",
                "references": symbol.references,
                "startLine": symbol.start_line,
                "endLine": symbol.end_line,
            }
            index.append(index_entry)

        # 保存索引
        index_path = self.output_dir / "_index.json"
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        print(f"知识构建完成: {len(symbols)} 个知识单元")
        print(f"  输出目录: {self.output_dir}")
        print(f"  Markdown: {len(index)} 个文件")
        print(f"  JSON metadata: {len(index)} 个文件")
        print(f"  索引: _index.json")

        return index


class GraphBuilder:
    """类型依赖图构建器"""

    def __init__(self, output_dir: str = "data/graph"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build(self, symbols: list[Symbol]) -> dict:
        """构建依赖图"""
        import networkx as nx

        G = nx.DiGraph()

        # 创建符号ID到符号的映射
        symbol_map = {s.id: s for s in symbols}

        # 添加节点
        for symbol in symbols:
            G.add_node(symbol.id, type=symbol.symbol_type, name=symbol.name)

        # 添加边（引用关系）
        for symbol in symbols:
            for ref in symbol.references:
                # 检查引用的符号是否存在于知识库中
                if ref in symbol_map:
                    G.add_edge(symbol.id, ref, relation="references")

        # 图统计
        graph_info = {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "isolated_nodes": sum(1 for d in G.degree() if d[1] == 0),
        }

        # 序列化图
        graph_data = {
            "graph_info": graph_info,
            "nodes": list(G.nodes(data=True)),
            "edges": list(G.edges(data=True)),
            "node_count": G.number_of_nodes(),
            "edge_count": G.number_of_edges(),
        }

        # 保存图数据
        graph_path = self.output_dir / "dependency_graph.json"
        serializable_graph = {
            "graph_info": graph_info,
            "nodes": [
                {"id": n, **data}
                for n, data in G.nodes(data=True)
            ],
            "edges": [
                {"source": u, "target": v, **data}
                for u, v, data in G.edges(data=True)
            ],
        }
        graph_path.write_text(
            json.dumps(serializable_graph, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        print(f"依赖图构建完成:")
        print(f"  节点: {graph_info['nodes']}")
        print(f"  边: {graph_info['edges']}")
        print(f"  孤立节点: {graph_info['isolated_nodes']}")

        return graph_data

    def expand(self, symbol_id: str, graph_data: dict, depth: int = 1) -> list[str]:
        """展开指定符号的依赖链"""
        expanded = set()
        current_level = {symbol_id}

        for _ in range(depth):
            next_level = set()
            for node_id in current_level:
                for edge in graph_data["edges"]:
                    if edge["source"] == node_id:
                        next_level.add(edge["target"])
            expanded |= current_level
            current_level = next_level

        return list(expanded)
