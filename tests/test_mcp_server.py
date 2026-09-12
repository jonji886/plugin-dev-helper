"""MCP Server 单元测试：不依赖远程 LLM，使用最小知识库夹具。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from mcp_server.runtime import build_container
from mcp_server.server import create_app, create_mcp_server
from tests.mcp_fixtures import build_fixture_container

EXPECTED_TOOLS = {
    "search_docs",
    "get_api",
    "get_type",
    "get_related_symbols",
    "get_examples",
    "validate_api_usage",
    "get_plugin_constraints",
    "validate_plugin_project",
    "get_plugin_scaffold",
    "probe_plugin_dev_server",
}


def run(coro):
    return asyncio.run(coro)


class McpServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp_path = Path(cls._tmp.name)
        cls.container = build_fixture_container(cls.tmp_path)
        cls.mcp = create_mcp_server(cls.container.settings, cls.container)
        cls.app = create_app(cls.container.settings, cls.container)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    async def _call(self, name: str, arguments: dict):
        result = await self.mcp.call_tool(name, arguments)
        if isinstance(result, tuple) and len(result) > 1:
            return result[1]
        return result[0].text

    def call(self, name: str, arguments: dict) -> dict:
        payload = run(self._call(name, arguments))
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        return payload

    # A / B：Server 能启动，/health 正常
    def test_health_endpoint(self):
        import httpx

        async def fetch():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/health")

        response = run(fetch())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service"], "plugin-developer-mcp")
        self.assertEqual(payload["transport"], "streamable-http")
        self.assertTrue(payload["knowledge_available"])
        self.assertGreaterEqual(payload["tool_count"], 9)

    def test_mcp_endpoint_mounted(self):
        paths = {getattr(route, "path", "") for route in self.app.routes}
        mounts = {getattr(route, "path", "") for route in self.app.routes}
        self.assertTrue({"/health", "/ready"} <= paths)
        self.assertTrue(any(path in ("/mcp", "/") for path in mounts))

    # DNS rebinding 防护：默认放行 localhost，拒绝公网 Host；配置 allowed_hosts 后可放行
    def test_transport_security_host_validation(self):
        from dataclasses import replace

        from starlette.testclient import TestClient

        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }

        def post_mcp(settings, host: str):
            # 每次新建 app（其内部的 SessionManager 只能 run 一次），
            # TestClient 的 with 会执行 lifespan 初始化任务组。
            app = create_app(settings, self.container)
            with TestClient(app, base_url=f"http://{host}") as client:
                return client.post(
                    "/mcp",
                    json=init_payload,
                    headers={"Content-Type": "application/json"},
                )

        # 默认 allowed_hosts 只含 localhost：公网 Host 被拒
        public = post_mcp(self.container.settings, "124.223.217.62:8011")
        self.assertEqual(public.status_code, 421)

        # localhost Host 正常放行
        local = post_mcp(self.container.settings, "127.0.0.1:8011")
        self.assertNotIn(local.status_code, (421, 403))

        # 配置 allowed_hosts 追加公网 Host 后放行
        open_settings = replace(
            self.container.settings,
            allowed_hosts=self.container.settings.allowed_hosts + ("124.223.217.62:*",),
        )
        allowed = post_mcp(open_settings, "124.223.217.62:8011")
        self.assertNotIn(allowed.status_code, (421, 403))

    # C：tools 可以被发现
    def test_tools_discoverable(self):
        tools = run(self.mcp.list_tools())
        self.assertEqual({tool.name for tool in tools}, EXPECTED_TOOLS)
        for tool in tools:
            self.assertTrue(tool.description)

    # D：search_docs
    def test_search_docs_returns_structured_results(self):
        payload = self.call("search_docs", {"query": "工具插件 UI 和 VM 怎么通信", "top_k": 3})
        self.assertEqual(payload["status"], "ok")
        self.assertGreater(payload["result_count"], 0)
        first = payload["results"][0]
        self.assertIn("source", first)
        self.assertIn("symbol", first)
        self.assertIn("content", first)

    # E：get_api exact match
    def test_get_api_exact_match(self):
        payload = self.call("get_api", {"symbol": "IDP.Miniapp.exit"})
        self.assertTrue(payload["found"])
        self.assertEqual(payload["match_type"], "exact")
        self.assertEqual(payload["symbol"], "IDP.Miniapp.exit")
        self.assertEqual(payload["sdk_version"], "1.83.0")
        self.assertEqual(payload["source_file"], "index.d.ts")
        self.assertEqual(payload["source_lines"]["start"], 4062)

    def test_get_api_with_parameters(self):
        payload = self.call("get_api", {"symbol": "IDP.Miniapp.uploadDataAsync"})
        self.assertTrue(payload["found"])
        self.assertEqual(payload["parameters"][0]["name"], "option")
        self.assertIn("MiniappUploadDataOption", payload["related_types"])

    # F：get_api 不存在时返回 not_found
    def test_get_api_not_found(self):
        payload = self.call("get_api", {"symbol": "IDP.Miniapp.exitMiniapp"})
        self.assertFalse(payload["found"])
        self.assertEqual(payload["status"], "not_found")
        self.assertTrue(payload["candidate_symbols"])
        self.assertNotIn("signature", payload)

    # G：get_type
    def test_get_type(self):
        payload = self.call("get_type", {"name": "MiniappUploadDataOption"})
        self.assertTrue(payload["found"])
        self.assertEqual(payload["kind"], "interface")
        names = {field["name"] for field in payload["fields"]}
        self.assertEqual(names, {"miniappId", "data"})
        self.assertTrue(all(field["required"] for field in payload["fields"]))
        self.assertEqual(payload["sdk_version"], "1.83.0")

    def test_get_type_not_found(self):
        payload = self.call("get_type", {"name": "NotExistOption"})
        self.assertFalse(payload["found"])
        self.assertEqual(payload["status"], "not_found")

    # H：related symbols
    def test_get_related_symbols(self):
        payload = self.call("get_related_symbols", {"symbol": "IDP.Miniapp.uploadDataAsync", "depth": 1})
        self.assertTrue(payload["found"])
        self.assertIn("MiniappUploadDataOption", payload["related_symbols"])
        self.assertEqual(payload["dependencies"][0]["symbol"], "MiniappUploadDataOption")

    # examples
    def test_get_examples(self):
        payload = self.call("get_examples", {"symbol": "IDP.Miniapp.exit", "language": "javascript"})
        self.assertTrue(payload["found"])
        self.assertTrue(all(item["verified_source"] for item in payload["examples"]))

    def test_get_examples_returns_empty_when_missing(self):
        payload = self.call("get_examples", {"symbol": "IDP.Miniapp.uploadDataAsync"})
        self.assertEqual(payload["examples"], [])
        self.assertEqual(payload["status"], "not_found")

    # I：validate_api_usage 能识别不存在的 API
    def test_validate_api_usage_detects_unknown_api(self):
        payload = self.call("validate_api_usage", {"code": "IDP.Miniapp.exitMiniapp();"})
        self.assertFalse(payload["valid"])
        types = {issue["type"] for issue in payload["issues"]}
        self.assertIn("unknown_api", types)
        self.assertEqual(payload["issues"][0]["severity"], "error")

    def test_validate_api_usage_accepts_known_usage(self):
        code = 'IDP.Miniapp.uploadDataAsync({ miniappId: "1", data: "x" });'
        payload = self.call("validate_api_usage", {"code": code, "sdk_version": "1.83.0"})
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["issues"], [])

    def test_validate_api_usage_detects_unknown_parameter(self):
        code = 'IDP.Miniapp.uploadDataAsync({ miniappId: "1", dat: "x" });'
        payload = self.call("validate_api_usage", {"code": code})
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["issues"][0]["type"], "unknown_parameter")
        self.assertIn("data", payload["issues"][0]["suggestions"])

    # J：Tool 内部异常不会导致 Server 崩溃
    def test_tool_exception_is_isolated(self):
        original = self.container.knowledge.resolve_deep

        def boom(_symbol):
            raise RuntimeError("boom")

        self.container.knowledge.resolve_deep = boom  # type: ignore[method-assign]
        try:
            payload = self.call("get_api", {"symbol": "IDP.Miniapp.exit"})
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error_type"], "RuntimeError")
            # 同一 Server 的其他 Tool 仍然可用
            healthy = self.call("get_api", {"symbol": "IDP.Design.getDesignId"})
            self.assertIn("status", healthy)
        finally:
            self.container.knowledge.resolve_deep = original  # type: ignore[method-assign]

        payload = self.call("get_api", {"symbol": "IDP.Miniapp.exit"})
        self.assertTrue(payload["found"])

    def test_telemetry_records_tool_calls(self):
        metrics = self.container.telemetry.metrics()
        self.assertTrue(metrics["enabled"])
        self.assertIn("get_api", metrics["tools"])


if __name__ == "__main__":
    unittest.main()
