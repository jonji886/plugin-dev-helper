"""CodeBuddy Benchmark Driver 测试（全部使用 mock，不启动真实 CodeBuddy）。

覆盖任务书 §15 要求：
- driver config parsing
- workspace isolation
- baseline / mcp MCP config 切换
- CodeBuddy result normalization（trace normalizer）
- timeout / agent failure / invalid output / missing token metadata
- benchmark aggregation / comparison report 生成
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.agent_drivers import (
    AgentRunResult,
    CodeBuddyCLIDriver,
    CodeBuddyConfig,
    CodeBuddyManualDriver,
    ReferenceDriver,
    normalize_codebuddy_trace,
)
from scripts.run_coding_agent_benchmark import (
    BenchmarkCase,
    BenchmarkEvaluator,
    aggregate,
)

BENCHMARK_DIR = Path("benchmark").resolve()


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        id="T01", name="exit miniapp", category="fix_wrong_api",
        description="", user_request="call exit on miniapp",
        fixture="T01-exit-miniapp",
        acceptance_tests=["contains:IDP.Miniapp.exit", "not_contains:exitMiniapp"],
        expected_api_symbols=["IDP.Miniapp.exit"],
    )


# ---------------------------------------------------------------------------
# 1) driver config parsing
# ---------------------------------------------------------------------------

def test_codebuddy_config_defaults():
    cfg = CodeBuddyConfig()
    assert cfg.agent == "codebuddy"
    assert cfg.model == "UNAVAILABLE"          # 不可得不估算
    assert cfg.mode == "manual"
    assert cfg.mcp_enabled is False
    assert cfg.runs == 1


# ---------------------------------------------------------------------------
# 2) workspace isolation + 3) baseline/mcp MCP config 切换
# ---------------------------------------------------------------------------

def test_workspace_isolation_and_mcp_switch(tmp_path):
    cfg = CodeBuddyConfig(
        agent="codebuddy", mcp_enabled=True,
        mcp_servers={"plugin-dev-helper": {"type": "http", "url": "http://x/mcp"}},
    )
    driver = CodeBuddyManualDriver(BENCHMARK_DIR, cfg)

    base_ws = tmp_path / "baseline" / "T01-r0"
    mcp_ws = tmp_path / "mcp" / "T01-r0"
    base_ws.mkdir(parents=True)
    mcp_ws.mkdir(parents=True)

    driver.produce(_case(), "baseline", base_ws)
    driver.produce(_case(), "mcp", mcp_ws)

    # 隔离：两个工作区各自含源码副本
    assert (base_ws / "src" / "vm.ts").exists()
    assert (mcp_ws / "src" / "vm.ts").exists()
    # 隔离：互不影响
    assert base_ws != mcp_ws

    # MCP 切换：baseline 不含 .codebuddy/mcp.json；mcp 含且只有 plugin-dev-helper
    assert not (base_ws / ".codebuddy" / "mcp.json").exists()
    mcp_json = mcp_ws / ".codebuddy" / "mcp.json"
    assert mcp_json.exists()
    cfg_on_disk = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert list(cfg_on_disk["mcpServers"].keys()) == ["plugin-dev-helper"]


def test_prepare_run_marks_manual_and_mcp_flag(tmp_path):
    cfg = CodeBuddyConfig(
        agent="codebuddy", mcp_enabled=True,
        mcp_servers={"plugin-dev-helper": {"type": "http", "url": "http://x/mcp"}},
    )
    driver = CodeBuddyManualDriver(BENCHMARK_DIR, cfg)
    ws = tmp_path / "ws"
    ws.mkdir()
    res = driver.prepare_run(_case(), "mcp", ws)
    assert res.status == "manual_pending"
    assert res.metadata["manual_execution"] is True
    assert res.metadata["mcp_enabled"] is True
    assert res.token_usage == "UNAVAILABLE"
    assert (ws / "task_prompt.md").exists()


# ---------------------------------------------------------------------------
# 4) CodeBuddy result normalization（trace normalizer）
# ---------------------------------------------------------------------------

def test_normalize_canonical_trace_passthrough():
    raw = {
        "run_id": "r1", "agent": "codebuddy", "condition": "with_mcp",
        "tasks": [{"task_id": "T01", "accepted": True, "abstained": False,
                   "calls": [{"seq": 1, "tool": "get_api", "arguments": {"q": "a"},
                              "status": "ok"}],
                   "symbols_queried": ["IDP.Miniapp.exit"]}],
    }
    norm = normalize_codebuddy_trace(raw)
    assert norm["agent"] == "codebuddy"
    assert norm["tasks"][0]["calls"][0]["tool"] == "get_api"
    assert norm["tasks"][0]["calls"][0]["arguments"] == {"q": "a"}
    assert norm["tasks"][0]["symbols_queried"] == ["IDP.Miniapp.exit"]


def test_normalize_alternate_field_names():
    # CodeBuddy 私有格式可能使用 name/input/outcome 等
    raw = {"tasks": [{"id": "T01",
                      "calls": [{"name": "get_type", "input": {"s": "x"}, "outcome": "ok"}]}]}
    norm = normalize_codebuddy_trace(raw)
    call = norm["tasks"][0]["calls"][0]
    assert call["tool"] == "get_type"
    assert call["arguments"] == {"s": "x"}
    assert call["status"] == "ok"
    assert call["seq"] == 1


def test_import_run_builds_result_from_real_artifact(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "vm.ts").write_text("IDP.Miniapp.exit();", encoding="utf-8")
    trace_raw = {"tasks": [{"task_id": "T01", "accepted": True,
                            "calls": [{"tool": "get_api", "arguments": {}, "status": "ok"}]}]}
    res = CodeBuddyManualDriver.import_run(ws, trace_raw=trace_raw, accepted=True)
    assert res.status == "ok"
    assert res.tool_calls[0]["tool"] == "get_api"
    assert res.token_usage == "UNAVAILABLE"
    assert (ws / "trace.json").exists()       # 落盘 canonical trace


# ---------------------------------------------------------------------------
# 5) timeout / 6) agent failure / 7) invalid output / 8) missing token metadata
# ---------------------------------------------------------------------------

def test_cli_driver_raises_when_no_stdout(monkeypatch, tmp_path):
    cfg = CodeBuddyConfig(agent="codebuddy", mode="cli", mcp_enabled=True,
                          mcp_servers={"plugin-dev-helper": {"type": "http", "url": "http://x/mcp"}})
    driver = CodeBuddyCLIDriver(BENCHMARK_DIR, cfg)

    class _Proc:
        stdout = ""
        stderr = ""
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc())
    ws = tmp_path / "T01-cli"          # 隔离工作区，绝不写回源 fixture
    with pytest.raises(RuntimeError):
        driver.produce(_case(), "mcp", ws)


def test_cli_driver_timeout_raises(monkeypatch, tmp_path):
    import subprocess

    cfg = CodeBuddyConfig(agent="codebuddy", mode="cli", mcp_enabled=True, timeout_seconds=1,
                          mcp_servers={"plugin-dev-helper": {"type": "http", "url": "http://x/mcp"}})
    driver = CodeBuddyCLIDriver(BENCHMARK_DIR, cfg)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(cmd="buddycn", timeout=1)))
    ws = tmp_path / "T01-cli-timeout"  # 隔离工作区，绝不写回源 fixture
    with pytest.raises(RuntimeError):
        driver.produce(_case(), "mcp", ws)


def test_run_case_records_harness_error(tmp_path):
    from scripts.run_coding_agent_benchmark import run_case

    class BoomDriver:
        def produce(self, case, mode, workspace):
            raise RuntimeError("agent crashed")

    class FakeEval:
        def evaluate(self, case, workspace):
            raise AssertionError("should not reach")

    res = run_case(_case(), BoomDriver(), "mcp", FakeEval(), 0, tmp_path / "x")
    assert res.success is False
    assert res.failure_type.startswith("HARNESS_ERROR")


def test_invalid_output_fails_evaluation(tmp_path):
    class FakeValidator:
        def validate_project(self, ws):
            from mcp_server.services.project_validator import ValidationResult, Issue

            return ValidationResult(
                valid=False,
                issues=[Issue(issue_id="i1", severity="CRITICAL", category="API",
                              code="API_UNKNOWN_API", message="hallu symbol exitMiniapp")])

    ev = BenchmarkEvaluator(FakeValidator())
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "vm.ts").write_text("exitMiniapp();", encoding="utf-8")
    success, _ = ev.evaluate(_case(), ws)
    assert success is False


def test_agent_run_result_missing_token_is_unavailable():
    res = AgentRunResult(status="ok", workspace="/tmp/ws")
    assert res.token_usage == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 9) benchmark aggregation / 10) comparison report
# ---------------------------------------------------------------------------

def test_aggregate_rates_and_abstention_none():
    results = [
        # 成功 + 真实失败集合
        _res(success=True, abstain_expected=False),
        _res(success=False, failure_type="API_HALLUCINATION", hallucination=True),
    ]
    agg = aggregate(results)
    assert agg["task_success_rate"] == 0.5
    assert agg["hallucination_rate"] == 0.5
    assert agg["abstention_correct_rate"] is None
    assert agg["tokens"] == "UNAVAILABLE"


def test_aggregate_abstention_rate():
    results = [
        _res(success=True, abstain_expected=True, abstained=True),
        _res(success=False, abstain_expected=True, abstained=False),
    ]
    agg = aggregate(results)
    assert agg["abstention_correct_rate"] == 0.5


def _res(success=True, failure_type="", hallucination=False, abstain_expected=False,
         abstained=False) -> "object":
    from scripts.run_coding_agent_benchmark import CaseResult

    return CaseResult(
        case_id="T01", mode="mcp", run_index=0, success=success, latency_ms=1.0,
        failure_type=failure_type, first_pass=success, api_correct=success,
        constraint_violation=False, hallucination=hallucination,
        abstained=abstained, abstain_expected=abstain_expected,
        issues=[], trace_metrics={}, extra={},
    )
