"""酷家乐工具插件 Guardrail 的 Rule、Validator 和 MCP Tool 测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
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

    def test_valid_plugin_has_no_findings_and_full_score(self):
        result = self.container.plugin_validator.validate(self.copy_fixture("valid_plugin"))
        self.assertTrue(result["passed"], result["findings"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["summary"], {"critical": 0, "high": 0, "medium": 0, "low": 0})

    def test_manifest_checks_cover_missing_invalid_and_entry_files(self):
        project = self.root / "manifest_errors"
        project.mkdir()
        result = self.container.plugin_validator.validate(project)
        self.assertEqual(result["findings"][0]["rule_id"], "KJL-MANIFEST-001")
        self.assertIn("manifest.json", result["findings"][0]["file"])

        (project / "manifest.json").write_text("{not-json", encoding="utf-8")
        result = self.container.plugin_validator.validate(project)
        self.assertEqual(result["findings"][0]["rule_id"], "KJL-MANIFEST-004")
        self.assertGreaterEqual(result["findings"][0]["line"], 1)

        (project / "manifest.json").write_text(json.dumps({
            "name": "x", "version": "1", "frame": "missing.txt", "main": "missing.ts"
        }), encoding="utf-8")
        result = self.container.plugin_validator.validate(project)
        ids = {finding["rule_id"] for finding in result["findings"]}
        self.assertIn("KJL-MANIFEST-002", ids)
        self.assertIn("KJL-MANIFEST-003", ids)

    def test_ui_vm_runtime_rules_return_location_risk_suggestion_and_confidence(self):
        result = self.container.plugin_validator.validate(self.copy_fixture("invalid_vm_dom"))
        ids = {finding["rule_id"] for finding in result["findings"]}
        self.assertTrue({"KJL-VM-001", "KJL-VM-002", "KJL-VM-003", "KJL-VM-004", "KJL-VM-005", "KJL-VM-006"} <= ids)
        finding = next(item for item in result["findings"] if item["rule_id"] == "KJL-VM-002")
        self.assertEqual(finding["file"], "vm.js")
        self.assertGreaterEqual(finding["line"], 1)
        self.assertTrue(finding["risk"])
        self.assertTrue(finding["suggestion"])
        self.assertIn(finding["confidence"], {"high", "medium", "low"})

        ui_result = self.container.plugin_validator.validate(self.copy_fixture("invalid_ui_api"))
        self.assertIn("KJL-UI-001", {item["rule_id"] for item in ui_result["findings"]})

    def test_communication_detects_both_directions_action_mismatch(self):
        result = self.container.plugin_validator.validate(self.copy_fixture("broken_message_flow"))
        findings = [item for item in result["findings"] if item["rule_id"] == "KJL-COMM-003"]
        self.assertGreaterEqual(len(findings), 2)
        self.assertTrue(all(item["details"].get("action") for item in findings))

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

    def test_inspect_project_recognizes_http_server_cors_flag(self):
        project = self.copy_fixture("valid_plugin")
        pkg = json.loads((project / "package.json").read_text(encoding="utf-8"))
        pkg["scripts"]["start"] = 'concurrently "webpack --watch" "http-server build/ -c-1 --cors"'
        (project / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        dev_server_file = project / "dev-server.js"
        if dev_server_file.is_file():
            dev_server_file.unlink()
        result = self.container.dev_server.inspect_project(project)
        rule_ids = {issue["rule_id"] for issue in result["issues"]}
        self.assertNotIn("KJL-DEV-003", rule_ids)
        self.assertNotIn("KJL-DEV-004", rule_ids)

    def test_dev_server_probe_checks_manifest_frame_main_and_cors(self):
        project = self.copy_fixture("valid_plugin")

        class CorsHandler(SimpleHTTPRequestHandler):
            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "https://miniapp-1258830046.file.myqcloud.com")
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
                super().end_headers()

            def do_OPTIONS(self):
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_args):
                return

        handler = lambda *args, **kwargs: CorsHandler(*args, directory=str(project), **kwargs)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.container.dev_server.probe(
                project,
                server_url=f"http://127.0.0.1:{server.server_port}",
                timeout_seconds=2,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertTrue(result["passed"], result["findings"])
        self.assertTrue({"manifest", "frame", "main"} <= {
            check["name"] for check in result["checks"] if check["name"] in {"manifest", "frame", "main"}
        })

    def test_static_validation_requires_start_script(self):
        project = self.copy_fixture("valid_plugin")
        (project / "package.json").write_text(
            json.dumps({"name": "missing-start", "scripts": {} }), encoding="utf-8"
        )
        result = self.container.plugin_validator.validate(project)
        self.assertIn("KJL-DEV-002", {item["rule_id"] for item in result["findings"]})

    def test_ui_external_http_url_is_rejected_but_local_server_url_is_allowed(self):
        project = self.copy_fixture("valid_plugin")
        (project / "ui.html").write_text(
            (project / "ui.html").read_text(encoding="utf-8")
            + '\n<script src="http://example.com/plugin.js"></script>\n',
            encoding="utf-8",
        )
        result = self.container.plugin_validator.validate(project)
        self.assertIn("KJL-NET-002", {item["rule_id"] for item in result["findings"]})

    def test_validate_plugin_project_tool_scans_fixture(self):
        async def invoke():
            result = await self.mcp.call_tool("validate_plugin_project", {
                "platform": "kujiale", "path": str(self.copy_fixture("invalid_manifest"))
            })
            return result[1] if isinstance(result, tuple) else json.loads(result[0].text)

        payload = run(invoke())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["findings"][0]["rule_id"], "KJL-MANIFEST-004")


if __name__ == "__main__":
    unittest.main()
