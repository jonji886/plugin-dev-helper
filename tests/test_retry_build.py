"""Runtime Retry 与 Build Gate 集成测试。

覆盖：
- 瞬时错误后重试成功
- 重试耗尽 → FAILED (RETRY_EXHAUSTED)
- 不可重试错误（Permission/Auth）→ 立即 FAILED，不重试
- Repairable（校验失败）走 Repair，不走 Retry
- Build 成功 / 失败 / 超时 / 跳过
- 静态校验通过 + Build 失败 → 最终 FAILED（Build Gate 阻断）
- Trace 记录 retry / build 事件
"""

from __future__ import annotations

import sys

from agent.runtime.errors import (
    ErrorTaxonomy,
    NonRetryableAgentError,
    RetryPolicy,
    RetryableAgentError,
)
from agent.runtime.models import Task
from agent.runtime.repair import BoundedRepairLoop, EvidenceBasedRepairDriver
from agent.runtime.repositories import TaskRepository
from agent.runtime.state_machine import TaskStatus
from agent.runtime.task_runtime import TaskRuntime
from tests.mcp_fixtures import build_fixture_container

VALID_FILES = {
    "manifest.json": '{"name":"demo-plugin","version":"1.0.0","frame":"frame.html","main":"vm.js"}',
    "package.json": '{"name":"demo","scripts":{"start":"node server.js"}}',
    "frame.html": "<html><body><button id='btn'>go</button></body></html>",
    "vm.js": "var IDP = require('@manycore/idp-sdk');\nIDP.Miniapp.exit();\n",
}

BUGGY_FILES = {
    "manifest.json": '{"name":"demo-plugin","version":"1.0.0","frame":"frame.html","main":"vm.js"}',
    "package.json": '{"name":"demo","scripts":{"start":"node server.js"}}',
    "frame.html": "<html><body><button id='btn'>go</button></body></html>",
    "vm.js": "var IDP = require('@manycore/idp-sdk');\nIDP.Miniapp.exitMiniapp();\n",
}


class _ScriptedAgent:
    def __init__(self, generate_files):
        self.generate_files = generate_files

    def generate(self, goal, task_id):
        return dict(self.generate_files)

    def repair(self, context):
        return dict(context.current_files)


class _FailingAgent:
    """generate 前 fail_times 次抛瞬时错误，之后返回 succeed_files。"""

    def __init__(self, fail_times, succeed_files=None):
        self.fail_times = fail_times
        self.succeed_files = succeed_files or {}
        self.generate_calls = 0

    def generate(self, goal, task_id):
        self.generate_calls += 1
        if self.generate_calls <= self.fail_times:
            raise RetryableAgentError("provider timeout")
        return dict(self.succeed_files)

    def repair(self, context):
        return dict(context.current_files)


class _NonRetryableAgent:
    def generate(self, goal, task_id):
        raise NonRetryableAgentError("401 unauthorized")

    def repair(self, context):
        return dict(context.current_files)


def _make_runtime(tmp_path, generate_files, *, retry_policy=None, build_cmd=None,
                  build_timeout_seconds=120.0, max_repair_attempts=2):
    container = build_fixture_container(tmp_path)
    repo = TaskRepository(tmp_path / "runtime.db")
    agent = _ScriptedAgent(generate_files)
    loop = BoundedRepairLoop(EvidenceBasedRepairDriver(), max_attempts=max_repair_attempts)
    return TaskRuntime(
        repo, container.project_validator, agent, loop,
        run_id="run-1", state_dir=str(tmp_path / "state"),
        retry_policy=retry_policy, build_cmd=build_cmd,
        sleeper=lambda *_: None, build_timeout_seconds=build_timeout_seconds,
    )


def _events(rt, task_id):
    return rt.repository.list_trace_events(task_id, "run-1")


def _types(rt, task_id):
    return [e.event_type for e in _events(rt, task_id)]


# ----------------------------------------------------------------- 重试

def test_retry_success_after_transient_failure(tmp_path):
    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=0.0, backoff_factor=1.0)
    agent = _FailingAgent(fail_times=1, succeed_files=VALID_FILES)
    container = build_fixture_container(tmp_path)
    rt = TaskRuntime(
        TaskRepository(tmp_path / "runtime.db"), container.project_validator, agent,
        BoundedRepairLoop(EvidenceBasedRepairDriver(), max_attempts=2),
        run_id="run-1", state_dir=str(tmp_path / "state"),
        retry_policy=policy, sleeper=lambda *_: None,
    )
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.COMPLETED.value
    assert agent.generate_calls == 2  # 第 1 次失败，第 2 次成功
    types = _types(rt, task.task_id)
    assert types.count("AGENT_CALL_STARTED") == 2
    assert types.count("AGENT_CALL_FAILED") == 1
    assert types.count("RETRY_SCHEDULED") == 1


def test_retry_exhausted_leads_to_failed(tmp_path):
    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=0.0, backoff_factor=1.0)
    agent = _FailingAgent(fail_times=3, succeed_files=VALID_FILES)
    container = build_fixture_container(tmp_path)
    rt = TaskRuntime(
        TaskRepository(tmp_path / "runtime.db"), container.project_validator, agent,
        BoundedRepairLoop(EvidenceBasedRepairDriver(), max_attempts=2),
        run_id="run-1", state_dir=str(tmp_path / "state"),
        retry_policy=policy, sleeper=lambda *_: None,
    )
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.FAILED.value
    assert agent.generate_calls == 3  # 3 次全部失败，耗尽重试
    types = _types(rt, task.task_id)
    assert types.count("AGENT_CALL_FAILED") == 3
    assert types.count("RETRY_SCHEDULED") == 2
    failed = [e for e in _events(rt, task.task_id) if e.event_type == "TASK_FAILED"][0]
    assert failed.metadata.get("reason") == "RETRY_EXHAUSTED"
    assert failed.metadata.get("attempts") == 3


def test_non_retryable_error_does_not_retry(tmp_path):
    container = build_fixture_container(tmp_path)
    rt = TaskRuntime(
        TaskRepository(tmp_path / "runtime.db"), container.project_validator,
        _NonRetryableAgent(),
        BoundedRepairLoop(EvidenceBasedRepairDriver(), max_attempts=2),
        run_id="run-1", state_dir=str(tmp_path / "state"),
        retry_policy=RetryPolicy(max_attempts=3), sleeper=lambda *_: None,
    )
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.FAILED.value
    types = _types(rt, task.task_id)
    assert "RETRY_SCHEDULED" not in types
    failed = [e for e in _events(rt, task.task_id) if e.event_type == "TASK_FAILED"][0]
    # 不可重试错误：reason 不是 RETRY_EXHAUSTED
    assert failed.metadata.get("reason") == "AGENT_CALL_FAILED"
    assert failed.error_type == ErrorTaxonomy.PERMISSION.value


def test_repairable_validation_error_goes_to_repair_not_retry(tmp_path):
    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=0.0, backoff_factor=1.0)
    rt = _make_runtime(tmp_path, BUGGY_FILES, retry_policy=policy)
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.COMPLETED.value
    assert final.repair_attempt >= 1
    types = _types(rt, task.task_id)
    # 校验失败属于 Repairable，走 Repair；generate 未抛异常，不应触发 Retry
    assert "REPAIR_STARTED" in types
    assert "AGENT_CALL_FAILED" not in types
    assert "RETRY_SCHEDULED" not in types


def test_retry_trace_order(tmp_path):
    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=0.0, backoff_factor=1.0)
    agent = _FailingAgent(fail_times=1, succeed_files=VALID_FILES)
    container = build_fixture_container(tmp_path)
    rt = TaskRuntime(
        TaskRepository(tmp_path / "runtime.db"), container.project_validator, agent,
        BoundedRepairLoop(EvidenceBasedRepairDriver(), max_attempts=2),
        run_id="run-1", state_dir=str(tmp_path / "state"),
        retry_policy=policy, sleeper=lambda *_: None,
    )
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    types = _types(rt, task.task_id)
    # 顺序：AGENT_CALL_STARTED -> AGENT_CALL_FAILED -> RETRY_SCHEDULED -> AGENT_CALL_STARTED -> ...
    seq = [t for t in types if t in {
        "AGENT_CALL_STARTED", "AGENT_CALL_FAILED", "RETRY_SCHEDULED"}]
    assert seq == [
        "AGENT_CALL_STARTED", "AGENT_CALL_FAILED", "RETRY_SCHEDULED", "AGENT_CALL_STARTED",
    ]


# ----------------------------------------------------------------- Build Gate

def test_build_success_verifies(tmp_path):
    rt = _make_runtime(tmp_path, VALID_FILES,
                       build_cmd=[sys.executable, "-c", "import sys; sys.exit(0)"])
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.COMPLETED.value
    assert final.build_verified is True
    assert "BUILD_COMPLETED" in _types(rt, task.task_id)


def test_build_failure_blocks_success(tmp_path):
    rt = _make_runtime(tmp_path, VALID_FILES,
                       build_cmd=[sys.executable, "-c", "import sys; sys.exit(3)"])
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.FAILED.value
    assert final.build_verified is False
    failed = [e for e in _events(rt, task.task_id) if e.event_type == "BUILD_FAILED"][0]
    assert failed.metadata.get("exit_code") == 3
    assert failed.error_type == ErrorTaxonomy.BUILD.value


def test_build_timeout_blocks_success(tmp_path):
    rt = _make_runtime(
        tmp_path, VALID_FILES,
        build_cmd=[sys.executable, "-c", "import time; time.sleep(5)"],
        build_timeout_seconds=0.4,
    )
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.FAILED.value
    failed = [e for e in _events(rt, task.task_id) if e.event_type == "BUILD_FAILED"][0]
    assert failed.metadata.get("timeout") is True


def test_build_skipped_is_not_pass(tmp_path):
    rt = _make_runtime(tmp_path, VALID_FILES, build_cmd=None)
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.COMPLETED.value
    # 跳过构建 ≠ 构建通过
    assert final.build_verified is False
    assert "BUILD_SKIPPED" in _types(rt, task.task_id)


def test_static_pass_plus_build_fail_is_failure(tmp_path):
    # 静态校验通过（VALID_FILES）但构建命令失败 -> Build Gate 必须阻断
    rt = _make_runtime(tmp_path, VALID_FILES,
                       build_cmd=[sys.executable, "-c", "import sys; sys.exit(1)"])
    task = rt.create_task("goal", str(tmp_path / "ws"))
    rt.run(task.task_id)
    final = rt.repository.get_task(task.task_id)
    assert final.status == TaskStatus.FAILED.value
    assert "BUILD_FAILED" in _types(rt, task.task_id)
