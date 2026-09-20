"""酷家乐工具插件 Guardrail 的 Rule 与 MCP Tool 测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from mcp_server.rules import KujialeRuleEngine
from mcp_server.server import create_mcp_server
from tests.mcp_fixtures import build_fixture_container

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "kujiale"


def run(coro):
    return asyncio.run(coro)


class KujialeGuardrailTests(unittest.TestCase):
    def test_rule_engine_loads_core_rules_and_filters_by_component(self):
        engine = KujialeRuleEngine()
        rule_ids = {rule.id for rule in engine.rules()}
        self.assertTrue({
            "KJL-MANIFEST-001", "KJL-MANIFEST-002", "KJL-MANIFEST-003",
            "KJL-MANIFEST-007",
            "KJL-UI-001", "KJL-VM-001", "KJL-VM-002", "KJL-VM-003",
            "KJL-VM-004", "KJL-VM-005", "KJL-VM-006",
            "KJL-COMM-001", "KJL-COMM-002", "KJL-COMM-003",
            "KJL-DEV-001", "KJL-DEV-002", "KJL-DEV-003", "KJL-DEV-004",
            "KJL-DEV-005", "KJL-DEV-006", "KJL-NET-001", "KJL-NET-002",
        } <= rule_ids)
        vm_rules = engine.query("vm", "VM 调用 API", limit=20)
        self.assertTrue(vm_rules)
        self.assertTrue(all(rule.scope == "vm" for rule in vm_rules))
        self.assertEqual(vm_rules[0].severity, "critical")

    def test_query_by_component_matches_category_not_only_scope(self):
        """scope 用于静态校验的文件定位（ui/vm），查询侧应仍能按 category 聚合。

        回归背景：KJL-COMM-001/002 的 scope 从 communication 改为 ui/vm 后，
        get_plugin_constraints(component=communication) 只剩 COMM-003，丢失核心通信约束。
        """
        engine = KujialeRuleEngine()
        comm_ids = {rule.id for rule in engine.query("communication", limit=20)}
        self.assertIn("KJL-COMM-001", comm_ids)
        self.assertIn("KJL-COMM-002", comm_ids)
        self.assertIn("KJL-COMM-003", comm_ids)
        # 高严重级别规则排序在前
        comm_rules = engine.query("communication", limit=20)
        self.assertEqual(comm_rules[0].severity, "high")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.container = build_fixture_container(self.root / "fixture")
        self.mcp = create_mcp_server(self.container.settings, self.container)

    def tearDown(self):
        self._tmp.cleanup()

    def copy_fixture(self, name: str) -> Path:
        import shutil

        destination = self.root / name
        shutil.copytree(FIXTURES / name, destination)
        return destination

    def test_constraint_and_scaffold_tools_are_real_mcp_tools(self):
        async def invoke():
            constraints = await self.mcp.call_tool("get_plugin_constraints", {
                "platform": "kujiale", "component": "vm", "task": "调用设计 API"
            })
            scaffold = await self.mcp.call_tool("get_plugin_scaffold", {
                "platform": "kujiale", "plugin_type": "tool_plugin", "task": "获取方案 JSON"
            })
            return constraints, scaffold

        constraints_result, scaffold_result = run(invoke())
        constraints = constraints_result[1] if isinstance(constraints_result, tuple) else json.loads(constraints_result[0].text)
        scaffold = scaffold_result[1] if isinstance(scaffold_result, tuple) else json.loads(scaffold_result[0].text)
        self.assertTrue(constraints["success"])
        self.assertTrue(all(item["severity"] in {"critical", "high", "medium", "low"}
                            for item in constraints["data"]["constraints"]))
        self.assertTrue(any(item["rule_id"] == "KJL-VM-001" for item in constraints["data"]["constraints"]))
        self.assertTrue(scaffold["success"])
        files = scaffold["data"]["files"]
        self.assertEqual(
            set(files),
            {"manifest.json", "page.html", "page.js", "vm.js", "package.json", "README.md"},
        )
        self.assertEqual(json.loads(files["manifest.json"])["frame"], "page.html")
        self.assertEqual(json.loads(files["manifest.json"])["main"], "vm.js")
        self.assertEqual(json.loads(files["package.json"])["scripts"]["start"], "http-server --cors -c-1")
        self.assertIn("window.parent.postMessage", files["page.js"])
        self.assertIn("defaultFrame.onMessageReceive", files["vm.js"])
        # 原生 HTML 模板使用 http-server 提供 CORS，不再自带 dev-server.js
        self.assertNotIn("dev-server.js", files)
        # 应提示先用 get_api 核实 IDP API 是否存在（避免 KJL-API-001）
        self.assertIn("guidance", scaffold["data"])
        joined_guidance = "".join(scaffold["data"]["guidance"])
        self.assertIn("get_api", joined_guidance)
        self.assertIn("KJL-API-001", joined_guidance)

    def test_scaffold_react_ts_webpack_stack_generates_expected_files(self):
        result = self.container.scaffold.build("获取方案信息", stack="react-ts-webpack")
        self.assertEqual(result["stack"], "react-ts-webpack")
        files = result["files"]
        self.assertEqual(
            set(files),
            {
                "manifest.json", "src/main.ts", "src/view.tsx", "src/page.html",
                "webpack.config.js", "tsconfig.json", "package.json", "README.md",
            },
        )
        manifest = json.loads(files["manifest.json"])
        self.assertEqual(manifest["frame"], "page.html")
        self.assertEqual(manifest["main"], "main.js")
        self.assertIn("window.parent.postMessage", files["src/view.tsx"])
        self.assertIn("defaultFrame.onMessageReceive", files["src/main.ts"])
        self.assertIn("ReactDOM.render", files["src/view.tsx"])
        pkg = json.loads(files["package.json"])
        self.assertIn("--cors", pkg["scripts"]["start"])
        self.assertEqual(pkg["dependencies"]["react"], "^17.0.2")
        self.assertEqual(pkg["devDependencies"]["@manycore/idp-sdk"], "^1.0.1")
        # 打包类插件的构建产物约束必须显式出现
        constraint_ids = {item["rule_id"] for item in result["constraints"]}
        self.assertIn("KJL-MANIFEST-007", constraint_ids)
        # 应提示先用 get_api 核实 IDP API 是否存在（避免 KJL-API-001）
        self.assertIn("guidance", result)
        joined_guidance = "".join(result["guidance"])
        self.assertIn("get_api", joined_guidance)
        self.assertIn("KJL-API-001", joined_guidance)

    def test_native_html_golden_template_matches_vanilla_scaffold(self):
        demo = PROJECT_ROOT / "demos" / "native-html"
        self.assertTrue(demo.is_dir(), "缺少 demos/native-html golden template")
        required = {"manifest.json", "page.html", "page.js", "vm.js", "package.json", "README.md"}
        self.assertEqual(required, set(p.name for p in demo.iterdir() if p.is_file()))
        manifest = json.loads((demo / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["frame"], "page.html")
        self.assertEqual(manifest["main"], "vm.js")
        pkg = json.loads((demo / "package.json").read_text(encoding="utf-8"))
        self.assertIn("--cors", pkg["scripts"]["start"])
        self.assertIn("@manycore/idp-sdk", pkg.get("devDependencies", {}))
        # golden template 应能被 vanilla 脚手架产物一致地复现
        result = self.container.scaffold.build("获取方案信息", stack="vanilla")
        for name in required:
            self.assertIn(name, result["files"])

    def test_scaffold_unsupported_stack_is_rejected(self):
        result = self.container.scaffold.build(task="x", stack="vue-ts")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "unsupported_stack")


if __name__ == "__main__":
    unittest.main()
