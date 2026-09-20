"""Task Runtime 集成测试：生成→校验→修复→复核、Checkpoint Resume、Repair Exhausted。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.runtime.models import RepairContext, Task
from agent.runtime.repair import BoundedRepairLoop, EvidenceBasedRepairDriver
from agent.runtime.repositories import TaskRepository
from agent.runtime.state_machine import TaskStatus
from agent.runtime.task_runtime import TaskAgent, TaskRuntime
from tests.mcp_fixtures import build_fixture_container


BUGGY_FILES = {
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


class ScriptedAgent:
    """确定性 Agent：generate 返回固定文件；repair 委托给 evidence driver。"""

    def __init__(self, generate_files: dict[str, str]):
        self.generate_files = generate_files

    def generate(self, goal: str, task_id: str) -> dict[str, str]:
        return dict(self.generate_files)

    def repair(self, context: RepairContext) -> dict[str, str]:
        return dict(context.current_files)


def _make_runtime(tmp_path, generate_files, max_repair_attempts=2):
    container = build_fixture_container(tmp_path)
    db = tmp_path / "runtime.db"
    repo = TaskRepository(db)
    agent = ScriptedAgent(generate_files)
    loop = BoundedRepairLoop(EvidenceBasedRepairDriver(), max_attempts=max_repair_attempts)
    return TaskRuntime(
        repo, container.project_validator, agent, loop,
        run_id="run-1", state_dir=str(tmp_path / "state"),
    )


def test_valid_plugin_completes_without_repair(tmp_path):
    rt = _make_runtime(tmp_path, VALID_FILES)
    task = rt.create_task("create a miniapp plugin", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.COMPLETED.value
    # 无修复发生
    assert final.repair_attempt == 0


def test_invalid_plugin_repaired_and_completed(tmp_path):
    rt = _make_runtime(tmp_path, BUGGY_FILES, max_repair_attempts=2)
    ws = tmp_path / "ws"
    task = rt.create_task("create a miniapp plugin", str(ws))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.COMPLETED.value
    assert final.repair_attempt >= 1
    # workspace 中 vm.js 已被修复为正确 API
    assert "IDP.Miniapp.exitMiniapp" not in (ws / "vm.js").read_text()
    assert "IDP.Miniapp.exit" in (ws / "vm.js").read_text()
    # 存在多版本 artifact
    v2 = rt.repository.get_artifact_version(final.task_id, final.run_id, "v2")
    assert v2 is not None


def test_checkpoint_resume_after_crash(tmp_path):
    """模拟进程在 VALIDATING 完成、准备 REPAIR 时退出，重启后从断点继续。"""
    rt = _make_runtime(tmp_path, BUGGY_FILES, max_repair_attempts=2)
    ws = tmp_path / "ws"
    task = rt.create_task("create a miniapp plugin", str(ws))
    # 运行到 status == REPAIRING 即模拟崩溃退出（不执行 repair）
    rt.run(task.task_id, halt_when=lambda t: t.status == TaskStatus.REPAIRING.value)
    mid = rt.repository.get_task(task.task_id)
    assert mid.status == TaskStatus.REPAIRING.value
    assert mid.artifact_version == "v1"  # 仅生成，未修复

    # 新 Runtime 实例（模拟进程重启），从 Checkpoint 恢复
    rt2 = _make_runtime(tmp_path, BUGGY_FILES, max_repair_attempts=2)
    rt2.resume(task.task_id)
    final = rt2.repository.get_task(task.task_id)
    assert final.status == TaskStatus.COMPLETED.value
    # 恢复后不应重新生成（仍基于 v1 修复）
    assert final.artifact_version in ("v2", "v3")


def test_repair_exhausted_leads_to_failed(tmp_path):
    """结构类错误（驱动无法确定性修复）耗尽修复次数后进入 FAILED，并保留 RCA。"""
    # 该错误：manifest.main 指向不存在文件，driver 不改 manifest 内容
    structural_files = dict(BUGGY_FILES)
    structural_files["manifest.json"] = json.dumps({
        "name": "demo-plugin", "version": "1.0.0",
        "frame": "frame.html", "main": "missing_vm.js",
    })
    rt = _make_runtime(tmp_path, structural_files, max_repair_attempts=2)
    task = rt.create_task("create a miniapp plugin", str(tmp_path / "ws2"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.FAILED.value
    # Root Cause 记录存在
    failures = rt.repository.list_failures(final.task_id)
    assert failures
    assert any(f.first_divergence for f in failures)


def test_state_machine_blocks_illegal_resume(tmp_path):
    rt = _make_runtime(tmp_path, VALID_FILES)
    task = rt.create_task("x", str(tmp_path / "ws3"))
    rt.run(task.task_id)
    # 已 COMPLETED，再尝试 repair 迁移应被状态机拒绝（运行时已在终态，run 直接返回）
    final_before = rt.repository.get_task(task.task_id)
    rt.run(task.task_id)
    final_after = rt.repository.get_task(task.task_id)
    assert final_before.status == final_after.status == TaskStatus.COMPLETED.value
