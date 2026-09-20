"""Project Validator 单元测试（确定性，不依赖 LLM）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.services.project_validator import ProjectValidator
from tests.mcp_fixtures import build_fixture_container


def _write_plugin(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


VALID_FILES = {
    "manifest.json": json.dumps({
        "name": "demo-plugin", "version": "1.0.0",
        "frame": "frame.html", "main": "vm.js",
    }),
    "package.json": json.dumps({"name": "demo", "scripts": {"start": "node server.js"}}),
    "frame.html": "<html><body><button id='btn'>go</button></body></html>",
    "vm.js": (
        "var IDP = require('@manycore/idp-sdk');\n"
        "IDP.Miniapp.exit();\n"
    ),
}

# 幻觉 API：IDP.Miniapp.exitMiniapp 不存在
HALLUCINATION_FILES = {
    "manifest.json": json.dumps({
        "name": "demo-plugin", "version": "1.0.0",
        "frame": "frame.html", "main": "vm.js",
    }),
    "package.json": json.dumps({"name": "demo", "scripts": {"start": "node server.js"}}),
    "frame.html": "<html><body><button id='btn'>go</button></body></html>",
    "vm.js": (
        "var IDP = require('@manycore/idp-sdk');\n"
        "IDP.Miniapp.exitMiniapp();\n"
    ),
}

# 结构错误：manifest 引用不存在的 main 文件
STRUCTURE_FILES = {
    "manifest.json": json.dumps({
        "name": "demo-plugin", "version": "1.0.0",
        "frame": "frame.html", "main": "missing_vm.js",
    }),
    "package.json": json.dumps({"name": "demo", "scripts": {"start": "node server.js"}}),
    "frame.html": "<html></html>",
}

# 平台规则违规：VM 文件直接操作 DOM
DOM_FILES = {
    "manifest.json": json.dumps({
        "name": "demo-plugin", "version": "1.0.0",
        "frame": "frame.html", "main": "vm.js",
    }),
    "package.json": json.dumps({"name": "demo", "scripts": {"start": "node server.js"}}),
    "frame.html": "<html></html>",
    "vm.js": (
        "var IDP = require('@manycore/idp-sdk');\n"
        "document.getElementById('btn').addEventListener('click', function(){});\n"
        "IDP.Miniapp.exit();\n"
    ),
}


@pytest.fixture()
def validator(tmp_path):
    container = build_fixture_container(tmp_path)
    return container.project_validator


def test_valid_plugin_passes(validator, tmp_path):
    root = tmp_path / "valid"
    _write_plugin(root, VALID_FILES)
    result = validator.validate_project(root)
    assert result.valid is True
    assert result.runtime_check in ("LOCAL_VALIDATION_PASS", "HOST_VALIDATION_REQUIRED")


def test_hallucinated_api_detected(validator, tmp_path):
    root = tmp_path / "hallu"
    _write_plugin(root, HALLUCINATION_FILES)
    result = validator.validate_project(root)
    assert result.valid is False
    codes = {i.code for i in result.issues}
    assert "API_UNKNOWN_API" in codes
    # 应定位到具体文件与行
    api_issue = next(i for i in result.issues if i.code == "API_UNKNOWN_API")
    assert api_issue.file == "vm.js"
    assert api_issue.line >= 2
    # evidence 应携带完整符号，供 Repair 使用
    assert "IDP.Miniapp.exitMiniapp" in api_issue.evidence
    # suggested_fix 应给出候选符号（形如 "X => Y"）
    assert "IDP.Miniapp.exit" in api_issue.suggested_fix


def test_structure_manifest_error(validator, tmp_path):
    root = tmp_path / "struct"
    _write_plugin(root, STRUCTURE_FILES)
    result = validator.validate_project(root)
    assert result.valid is False
    codes = {i.code for i in result.issues}
    assert "KJL-MANIFEST-003" in codes  # main 指向文件不存在


def test_rule_violation_dom_in_vm(validator, tmp_path):
    root = tmp_path / "dom"
    _write_plugin(root, DOM_FILES)
    result = validator.validate_project(root)
    assert result.valid is False
    codes = {i.code for i in result.issues}
    assert "KJL-VM-001" in codes  # VM 直接操作 DOM


def test_issue_schema_complete(validator, tmp_path):
    root = tmp_path / "hallu"
    _write_plugin(root, HALLUCINATION_FILES)
    result = validator.validate_project(root)
    payload = result.as_dict()
    assert "valid" in payload and "issues" in payload and "summary" in payload
    for issue in payload["issues"]:
        for field in ("issue_id", "severity", "category", "code", "message", "repairable"):
            assert field in issue
    # severity 收窄到枚举
    assert payload["issues"][0]["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "WARNING"}
