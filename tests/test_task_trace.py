"""Task Trace 与 Root Cause Analysis（First Divergence Principle）测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.runtime.models import TraceEvent
from agent.runtime.repositories import TaskRepository
from agent.runtime.trace import (
    TaskTraceRecorder,
    analyze_root_cause,
    classify_issue,
)
from agent.runtime.errors import ErrorTaxonomy
from mcp_server.services.project_validator import Issue, ValidationResult


def _make_repo(tmp_path):
    return TaskRepository(tmp_path / "trace.db")


def test_classify_issue_maps_to_taxonomy():
    api = Issue("x", "HIGH", "API", "API_UNKNOWN_API", "m", file="vm.js",
                evidence="symbol=IDP.Miniapp.exitMiniapp", suggested_fix="IDP.Miniapp.exitMiniapp => IDP.Miniapp.exit")
    assert classify_issue(api) == ErrorTaxonomy.API_HALLUCINATION
    manifest = Issue("x", "CRITICAL", "MANIFEST", "KJL-MANIFEST-003", "m")
    assert classify_issue(manifest) == ErrorTaxonomy.MANIFEST
    rule = Issue("x", "HIGH", "RULE", "KJL-VM-002", "m")
    assert classify_issue(rule) == ErrorTaxonomy.RULE_VIOLATION


def test_first_divergence_prefers_api_hallucination_over_build(tmp_path):
    """下游 symptom（BUILD_FAILED）不应掩盖首个偏差（API 幻觉）。"""
    repo = _make_repo(tmp_path)
    task_id, run_id = "t1", "r1"
    recorder = TaskTraceRecorder(repo, task_id, run_id)
    recorder.record("TASK_CREATED")
    recorder.record("ARTIFACT_CREATED", output_summary="v1")
    # 下游：Build 失败（symptom）
    recorder.record("BUILD_FAILED", status="error", error_type="BUILD",
                    error_message="tsc: exit code 1")
    # Validator 结果：首个 error 是 API 幻觉
    result = ValidationResult(valid=False)
    result.issues.append(Issue(
        "API-1", "HIGH", "API", "API_UNKNOWN_API",
        "使用了不存在的 API", file="vm.js", line=3,
        evidence="symbol=IDP.Miniapp.exitMiniapp",
        suggested_fix="IDP.Miniapp.exitMiniapp => IDP.Miniapp.exit",
    ))
    record = analyze_root_cause(repo, task_id, run_id, result)
    assert record is not None
    assert record.first_divergence is True
    assert record.failure_type == ErrorTaxonomy.API_HALLUCINATION.value
    assert record.failure_stage == "validate"


def test_first_divergence_from_trace_event(tmp_path):
    """当 trace 早期出现 MODEL/PROVIDER 异常，优先定位为 Provider 偏差。"""
    repo = _make_repo(tmp_path)
    task_id, run_id = "t2", "r2"
    recorder = TaskTraceRecorder(repo, task_id, run_id)
    recorder.record("MODEL_CALL", status="error", error_type="PROVIDER",
                    error_message="upstream 503")
    record = analyze_root_cause(repo, task_id, run_id)
    assert record is not None
    assert record.failure_type == ErrorTaxonomy.PROVIDER.value
    assert record.recommended_action == "retry"
