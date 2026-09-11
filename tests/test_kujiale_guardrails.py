"""酷家乐工具插件 Guardrail 的 Rule、Validator 和 MCP Tool 测试。"""

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
            "KJL-UI-001", "KJL-VM-001", "KJL-VM-002", "KJL-VM-003",
            "KJL-VM-004", "KJL-VM-005", "KJL-VM-006",
            "KJL-COMM-001", "KJL-COMM-002", "KJL-COMM-003",
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
        self.assertEqual(set(files), {"manifest.json", "ui.html", "vm.js"})
        self.assertEqual(json.loads(files["manifest.json"])["frame"], "ui.html")

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
