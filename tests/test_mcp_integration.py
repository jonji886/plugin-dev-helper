"""MCP 真实知识库集成测试。

使用本地已构建的 data/knowledge + data/graph + data/chroma。
data/ 不进入 Git，未构建知识库时整体跳过。
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def real_knowledge_available() -> bool:
    index_path = PROJECT_ROOT / "data" / "knowledge" / "_index.json"
    if not index_path.exists():
        return False
    try:
        return len(json.loads(index_path.read_text(encoding="utf-8"))) > 0
    except json.JSONDecodeError:
        return False


@unittest.skipUnless(real_knowledge_available(), "未检测到已构建的知识库（data/knowledge）")
class McpRealKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mcp_server.config import get_mcp_settings
        from mcp_server.runtime import build_container
        from mcp_server.server import create_mcp_server

        cls.settings = get_mcp_settings()
        cls.container = build_container(cls.settings)
        cls.mcp = create_mcp_server(cls.settings, cls.container)

    def call(self, name: str, arguments: dict) -> dict:
        async def invoke():
            result = await self.mcp.call_tool(name, arguments)
            if isinstance(result, tuple) and len(result) > 1:
                return result[1]
            return json.loads(result[0].text)

        return asyncio.run(invoke())

    def test_get_api_real_symbol(self):
        payload = self.call("get_api", {"symbol": "IDP.Miniapp.exit"})
        self.assertTrue(payload["found"])
        self.assertEqual(payload["sdk_version"], "1.83.0")
        self.assertTrue(payload["source_file"])
        self.assertGreater(payload["source_lines"]["start"], 0)

    def test_get_type_real_symbol(self):
        payload = self.call("get_type", {"name": "MiniappUploadDataOption"})
        self.assertTrue(payload["found"])
        self.assertTrue(payload["fields"])

    def test_get_related_symbols_real_graph(self):
        payload = self.call("get_related_symbols", {"symbol": "IDP.Miniapp.uploadDataAsync", "depth": 1})
        self.assertTrue(payload["found"])
        self.assertIn("MiniappUploadDataOption", payload["related_symbols"])

    def test_validate_official_doc_snippet_has_no_error(self):
        doc = (PROJECT_ROOT / "docs" / "rag" / "工具插件代码结构.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```(?:js|javascript)\n(.*?)```", doc, re.S)
        if not blocks:
            self.skipTest("官方文档中没有 JS 代码块")
        payload = self.call("validate_api_usage", {"code": "\n".join(blocks), "sdk_version": "1.83.0"})
        self.assertTrue(payload["valid"], json.dumps(payload["issues"], ensure_ascii=False))

    def test_search_docs_hybrid(self):
        if not self.container.retrieval.is_available():
            self.skipTest("未检测到可用向量库（data/chroma）")
        payload = self.call("search_docs", {"query": "工具插件 UI 和 VM 怎么通信", "top_k": 3})
        self.assertEqual(payload["mode"], "hybrid")
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["results"][0]["source"])


if __name__ == "__main__":
    unittest.main()
