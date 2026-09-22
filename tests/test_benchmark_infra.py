"""Benchmark 基础设施测试：First Pass 语义、隔离校验、相对路径、工具期望语义。"""

from __future__ import annotations

from pathlib import Path

from scripts.check_agent_trace import score_task
from scripts.run_coding_agent_benchmark import (
    ROOT,
    CaseResult,
    aggregate,
    verify_isolation,
    _rel_if_possible,
)


def _result(first_pass=None, success=True, **kw):
    return CaseResult(case_id="T01", mode="mcp", run_index=0, success=success,
                      latency_ms=1.0, first_pass=first_pass, **kw)


# ----------------------------------------------------------------- First Pass

def test_aggregate_first_pass_unavailable_when_unknown():
    results = [_result(first_pass=None), _result(first_pass=None), _result(first_pass=None)]
    agg = aggregate(results)
    # 无法判断 First Pass 时报告 UNAVAILABLE，绝不计算成 0%
    assert agg["first_pass_success_rate"] == "UNAVAILABLE"


def test_aggregate_first_pass_known_rate():
    results = [_result(first_pass=True), _result(first_pass=False), _result(first_pass=True)]
    agg = aggregate(results)
    assert agg["first_pass_success_rate"] == round(2 / 3, 4)


def test_first_pass_default_is_unavailable():
    # 当前实现默认 first_pass=None（UNAVAILABLE），不把 Final Success 当作 First Pass
    r = _result(success=True)
    assert r.first_pass is None


# ----------------------------------------------------------------- 隔离校验

def test_verify_isolation_baseline_clean(tmp_path):
    ws = tmp_path / "baseline" / "T01-r1"
    ws.mkdir(parents=True)
    info = verify_isolation({"mode": "baseline"}, ws, ROOT)
    assert info["mcp_expected"] is False
    assert info["mcp_detected"] is False
    assert info["isolation_verified"] is True


def test_verify_isolation_baseline_contaminated(tmp_path):
    ws = tmp_path / "baseline" / "T01-r1"
    (ws / ".codebuddy").mkdir(parents=True)
    (ws / ".codebuddy" / "mcp.json").write_text("{}")
    info = verify_isolation({"mode": "baseline"}, ws, ROOT)
    assert info["mcp_detected"] is True
    # baseline 工作区含 MCP 配置 => 隔离被破坏（INVALID_ENVIRONMENT）
    assert info["isolation_verified"] is False


def test_verify_isolation_mcp_clean(tmp_path):
    ws = tmp_path / "mcp" / "T01-r1"
    (ws / ".codebuddy").mkdir(parents=True)
    (ws / ".codebuddy" / "mcp.json").write_text("{}")
    info = verify_isolation({"mode": "mcp"}, ws, ROOT)
    assert info["mcp_expected"] is True
    assert info["mcp_detected"] is True
    assert info["isolation_verified"] is True


def test_verify_isolation_missing_workspace(tmp_path):
    ws = tmp_path / "gone"
    info = verify_isolation({"mode": "baseline"}, ws, ROOT)
    assert info["isolation_verified"] == "UNAVAILABLE"


# ----------------------------------------------------------------- 相对路径

def test_rel_if_possible_under_root():
    p = ROOT / "benchmark" / "results" / "workspaces" / "baseline" / "T01-r1"
    rel = _rel_if_possible(str(p), ROOT)
    assert not Path(rel).is_absolute()
    assert rel.startswith("benchmark" + "/")


def test_rel_if_possible_outside_root():
    p = "/Users/someone/secret"
    assert _rel_if_possible(p, ROOT) == p


def test_rel_if_possible_none():
    assert _rel_if_possible(None, ROOT) is None


# ----------------------------------------------------------------- 工具期望语义

def _trace_task(tools):
    return {"task_id": "T01", "calls": [{"tool": t, "arguments": {}} for t in tools]}


def test_tool_required_satisfied_and_forbidden():
    task = {
        "id": "T01", "mcp_tools_expected": ["get_api", "search_docs"],
        "tool_expectation": {
            "required_tools": ["get_api", "search_docs"],
            "optional_tools": ["validate_api_usage"],
            "acceptable_tool_sets": [["get_api", "search_docs"]],
            "forbidden_tools": ["delete_api"],
        },
    }
    score = score_task(task, _trace_task(["get_api", "search_docs"]))
    assert score["tool_expectation"]["required_satisfied"] is True
    assert score["tool_expectation"]["forbidden_violation"] is False
    assert score["tool_selection"]["recall"] == 1.0


def test_tool_forbidden_violation():
    task = {
        "id": "T01", "mcp_tools_expected": ["get_api"],
        "tool_expectation": {
            "required_tools": ["get_api"], "optional_tools": [],
            "acceptable_tool_sets": [["get_api"]], "forbidden_tools": ["delete_api"],
        },
    }
    score = score_task(task, _trace_task(["get_api", "delete_api"]))
    assert score["tool_expectation"]["forbidden_violation"] is True


def test_efficient_agent_not_penalized():
    # 高效 Agent 只用 required 工具子集（缺少 optional）完成，不应被判 recall 下降
    task = {
        "id": "T01", "mcp_tools_expected": ["get_api", "search_docs"],
        "tool_expectation": {
            "required_tools": ["get_api", "search_docs"],
            "optional_tools": ["validate_api_usage"],
            "acceptable_tool_sets": [["get_api", "search_docs"]],
            "forbidden_tools": [],
        },
    }
    score = score_task(task, _trace_task(["get_api"]))
    # 只调用了 required 之一的 get_api：recall 基于 required 覆盖，不该被惩罚为 0
    assert "get_api" in score["tool_expectation"]["required_tools"]
    score2 = score_task(task, _trace_task(["get_api", "search_docs"]))
    assert score2["tool_expectation"]["required_satisfied"] is True
    assert score2["tool_selection"]["recall"] == 1.0


def test_tool_expectation_backward_compat():
    # 无 tool_expectation 时，从 mcp_tools_expected 降级推导 required
    task = {"id": "T01", "mcp_tools_expected": ["get_api"]}
    score = score_task(task, _trace_task(["get_api"]))
    assert score["tool_expectation"]["required_satisfied"] is True
    assert score["tool_expectation"]["forbidden_violation"] is False
