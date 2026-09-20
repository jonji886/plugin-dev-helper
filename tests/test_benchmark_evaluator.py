"""Benchmark Harness / Evaluator 测试。

数据集为「源码级 vm.ts 修改」任务：验收以 acceptance 字符串断言 + 源码
API/规则确定性校验为准，不依赖完整工程 manifest。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_coding_agent_benchmark import (
    BenchmarkCase,
    BenchmarkEvaluator,
    ReferenceDriver,
    aggregate,
    load_cases,
    run_case,
)

BENCHMARK_DIR = Path("benchmark").resolve()


def _t07() -> BenchmarkCase:
    return BenchmarkCase(
        id="T07", name="fix wrong api", category="fix_wrong_api",
        description="", user_request="fix exitMiniapp -> exit",
        fixture="T07-fix-wrong-api",
        acceptance_tests=["contains:IDP.Miniapp.exit", "not_contains:exitMiniapp"],
        expected_api_symbols=["IDP.Miniapp.exit"],
    )


@pytest.fixture()
def evaluator(tmp_path):
    from tests.mcp_fixtures import build_fixture_container
    container = build_fixture_container(tmp_path / "_c")
    return BenchmarkEvaluator(container.project_validator), container


def test_load_cases_reads_dataset():
    cases = load_cases(BENCHMARK_DIR / "tasks.json")
    assert len(cases) >= 10
    assert all(c.id and c.user_request and c.acceptance_tests for c in cases)
    # T07 是「修复错误 API」类，初始态应当失败
    t07 = next(c for c in cases if c.id == "T07")
    assert t07.initial_expect_fail is True
    assert "not_contains:exitMiniapp" in t07.acceptance_tests


def test_reference_driver_mcp_overlays_solution(tmp_path):
    driver = ReferenceDriver(BENCHMARK_DIR)
    files = driver.produce(_t07(), "mcp", tmp_path / "ws")
    assert files
    blob = "\n".join(files.values())
    # 应用 solution 后，源码不再含幻觉符号，且含正确 API
    assert "exitMiniapp" not in blob
    assert "IDP.Miniapp.exit" in blob


def test_evaluator_mcp_solution_passes(evaluator, tmp_path):
    ev, _ = evaluator
    ws = tmp_path / "ws_mcp"
    ReferenceDriver(BENCHMARK_DIR).produce(_t07(), "mcp", ws)
    success, result = ev.evaluate(_t07(), ws)
    assert success is True, [i.to_dict() for i in result.issues]


def test_evaluator_baseline_fixture_fails(evaluator, tmp_path):
    ev, _ = evaluator
    ws = tmp_path / "ws_base"
    ReferenceDriver(BENCHMARK_DIR).produce(_t07(), "baseline", ws)
    success, _ = ev.evaluate(_t07(), ws)
    # baseline 原样 fixture 含幻觉符号 exitMiniapp，应失败
    assert success is False


def test_aggregate_and_run_case(evaluator, tmp_path):
    ev, _ = evaluator
    driver = ReferenceDriver(BENCHMARK_DIR)
    res = run_case(_t07(), driver, "mcp", ev, 0, tmp_path / "_runs")
    assert res.success is True
    assert res.abstain_expected is False
    agg = aggregate([res])
    assert agg["task_success_rate"] == 1.0
    assert agg["abstention_correct_rate"] is None
