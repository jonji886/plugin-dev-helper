"""MCP 测试用最小知识库夹具（不依赖真实 data/ 与远程 LLM）。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from mcp_server.config import get_mcp_settings
from mcp_server.runtime import build_container

SYMBOLS = [
    {
        "id": "IDP.Miniapp.exit",
        "name": "exit",
        "type": "function",
        "namespace": "IDP.Miniapp",
        "source": "index.d.ts",
        "sdkVersion": "1.83.0",
        "description": "退出小程序",
        "parameters": [],
        "properties": [],
        "methods": [],
        "enumMembers": [],
        "references": [],
        "aliases": ["exit", "IDP.exit", "IDP.Miniapp.exit"],
        "startLine": 4062,
        "endLine": 4062,
    },
    {
        "id": "IDP.Miniapp.uploadDataAsync",
        "name": "uploadDataAsync",
        "type": "function",
        "namespace": "IDP.Miniapp",
        "source": "index.d.ts",
        "sdkVersion": "1.83.0",
        "description": "临时存储数据",
        "parameters": [
            {"name": "option", "type": "MiniappUploadDataOption", "optional": False, "description": ""}
        ],
        "properties": [],
        "methods": [],
        "enumMembers": [],
        "references": ["MiniappUploadDataOption"],
        "aliases": ["uploadDataAsync", "IDP.Miniapp.uploadDataAsync"],
        "startLine": 4480,
        "endLine": 4480,
    },
    {
        "id": "MiniappUploadDataOption",
        "name": "MiniappUploadDataOption",
        "type": "interface",
        "namespace": "",
        "source": "index.d.ts",
        "sdkVersion": "1.83.0",
        "description": "上传数据参数",
        "parameters": [],
        "properties": [
            {"name": "miniappId", "type": "string", "optional": False, "description": "小程序 ID"},
            {"name": "data", "type": "string", "optional": False, "description": "数据"},
        ],
        "methods": [],
        "enumMembers": [],
        "references": [],
        "aliases": ["MiniappUploadDataOption"],
        "startLine": 18686,
        "endLine": 18689,
    },
    {
        "id": "rag.工具插件代码结构",
        "name": "工具插件代码结构",
        "type": "document",
        "namespace": "docs.rag",
        "source": "docs/rag/工具插件代码结构.md",
        "sdkVersion": "",
        "description": "工具插件 UI 与 VM 通信",
        "parameters": [],
        "properties": [],
        "methods": [],
        "enumMembers": [],
        "references": [],
        "aliases": ["工具插件代码结构"],
        "startLine": 1,
        "endLine": 20,
    },
]

RAG_DOC = """# 工具插件代码结构

UI 与 VM 通过 postMessage 通信。

```javascript
window.parent.postMessage({ action: "getDesignJsonUrl" }, "*");
IDP.Miniapp.exit();
```
"""


def build_fixture_container(tmp_path: Path):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    rag_dir = tmp_path / "docs" / "rag"
    rag_dir.mkdir(parents=True, exist_ok=True)

    index_entries = []
    for symbol in SYMBOLS:
        safe_id = symbol["id"].replace(".", "_")
        (knowledge_dir / f"{safe_id}.json").write_text(
            json.dumps(symbol, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        content = f"# {symbol['id']}\n\n{symbol['description']}\n"
        if symbol["properties"]:
            content += "\n## 属性\n\n| 名称 | 类型 |\n|---|---|\n"
            for prop in symbol["properties"]:
                content += f"| {prop['name']} | `{prop['type']}` |\n"
        (knowledge_dir / f"{safe_id}.md").write_text(content, encoding="utf-8")
        index_entries.append({
            **symbol,
            "mdFile": f"{safe_id}.md",
            "jsonFile": f"{safe_id}.json",
            "contentHash": "test",
        })
    (knowledge_dir / "_index.json").write_text(
        json.dumps(index_entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (rag_dir / "工具插件代码结构.md").write_text(RAG_DOC, encoding="utf-8")

    graph = {
        "graph_info": {"nodes": len(SYMBOLS), "edges": 1},
        "nodes": [{"id": item["id"], "type": item["type"], "name": item["name"]} for item in SYMBOLS],
        "edges": [
            {
                "source": "IDP.Miniapp.uploadDataAsync",
                "target": "MiniappUploadDataOption",
                "relation": "references",
            }
        ],
    }
    graph_path = graph_dir / "dependency_graph.json"
    graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    settings = replace(
        get_mcp_settings(),
        knowledge_path=knowledge_dir,
        graph_path=graph_path,
        chroma_path=tmp_path / "chroma",
        database_path=tmp_path / "mcp_metrics.sqlite3",
        rag_docs_path=rag_dir,
        telemetry_enabled=True,
    )
    return build_container(settings)
