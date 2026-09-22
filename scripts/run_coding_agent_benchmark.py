"""Coding Agent Benchmark Harness：Baseline vs MCP（确定性评估 + 真实 CodeBuddy Driver）。

设计原则（ADR-004）：
- 控制变量：两组实验唯一差异是「是否可访问 plugin-dev-helper MCP」。
- 公平：相同任务、相同初始 fixture、相同 Project Validator、相同 Evaluator。
- 确定性：结果由 Project Validator + acceptance 判定，不依赖 LLM Judge。
- 诚实：任何不可得数据标记 UNAVAILABLE；任何未执行数据标记 NOT_RUN；绝不伪造。

Driver 分层（scripts/agent_drivers/）：
- reference（默认）：确定性 stand-in，mcp 应用参考解、baseline 原样 fixture。
  产物只能解读为 harness 自检，不是 Agent 能力分数。
- codebuddy-manual：Harness 准备隔离工作区 + prompt + MCP 配置，由真实 CodeBuddy
  执行并回灌 artifact + trace。标记 manual_execution=true。
- codebuddy-cli：真实 `buddycn chat` 调用；本环境该 CLI 不向 stdout 返回结果，无法
  headless 采集，故自动判 NOT_RUN（不伪造）。

真实 Coding Agent Benchmark 的执行状态由 `codebuddy-manual --prepare/--import` 决定。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# 允许以脚本方式直接 import 项目模块（必须在项目 import 之前完成）
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.services.project_validator import ValidationResult  # noqa: E402

from mcp_server.config import get_mcp_settings  # noqa: E402
from mcp_server.runtime import build_container  # noqa: E402
from scripts.agent_drivers import (  # noqa: E402
    AgentRunResult,
    CodeBuddyCLIDriver,
    CodeBuddyConfig,
    CodeBuddyManualDriver,
    ReferenceDriver,
    normalize_codebuddy_trace,
)


@dataclass
class BenchmarkCase:
    id: str
    name: str
    category: str
    description: str
    user_request: str
    fixture: str
    sdk_version: str = ""
    expected_behavior: str = ""
    acceptance_tests: list[str] = field(default_factory=list)       # "contains:X" / "not_contains:X"
    expected_api_symbols: list[str] = field(default_factory=list)   # reference_symbols
    initial_expect_fail: bool = True
    allow_abstention: bool = False


# 需要「正确拒答 / 先查询」的类别，用 abstention 指标评估
ABSTENTION_CATEGORIES = {"reject_hallucination", "insufficient_info"}


@dataclass
class CaseResult:
    case_id: str
    mode: str
    run_index: int
    success: bool
    latency_ms: float
    tokens: str = "UNAVAILABLE"
    failure_type: str = ""
    # First Pass Success：首次候选实现未经修复即通过验收。当前 CodeBuddy trace 无法可靠区分
    # 「首次候选实现」与「后续自修复」，因此统一为 UNAVAILABLE（None），不伪造成功率。
    first_pass: bool | None = None
    api_correct: bool = True
    constraint_violation: bool = False
    hallucination: bool = False
    abstained: bool = False
    abstain_expected: bool = False
    issues: list[dict] = field(default_factory=list)
    trace_metrics: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class BenchmarkEvaluator:
    def __init__(self, validator):
        self.validator = validator

    def evaluate(self, case: BenchmarkCase, workspace: Path) -> tuple[bool, ValidationResult]:
        """数据集为「源码级 vm.ts 修改」任务，验收以 acceptance 字符串断言 +
        源码 API/规则确定性校验为准；不要求完整工程 manifest（fixture 本身无 manifest）。
        """
        result = self.validator.validate_project(workspace)
        blob = _workspace_blob(workspace)
        success = self._is_success(case, result, blob)
        return success, result

    @staticmethod
    def _is_success(case: BenchmarkCase, result: ValidationResult, blob: str) -> bool:
        for test in case.acceptance_tests:
            if not _test_pass(test, blob):
                return False
        if _source_api_errors(result):
            return False
        if _source_rule_errors(result):
            return False
        return True


def _test_pass(test: str, blob: str) -> bool:
    if test.startswith("contains:"):
        return test[len("contains:"):] in blob
    if test.startswith("not_contains:"):
        return test[len("not_contains:"):] not in blob
    if test.startswith("regex:"):
        return re.search(test[len("regex:"):], blob) is not None
    return test in blob


HALLUCINATION_CODES = {
    "API_UNKNOWN_API", "API_UNKNOWN_PARAMETER",
    "API_MISSING_REQUIRED_FIELD", "API_MISSING_REQUIRED_PARAMETER",
    "API_UNVERIFIED_MEMBER",
}


def _source_api_errors(result: ValidationResult) -> list:
    return [i for i in result.issues
            if i.category == "API" and i.severity in {"CRITICAL", "HIGH"}]


# 工程完整性类规则前缀：这些任务只改源码 vm.ts，是否具备 manifest/package/HTTP 服务
# 不在本任务的验收范围内（数据集本身为源码级 fixture）。
_PROJECT_COMPLETENESS_PREFIXES = ("KJL-DEV-", "KJL-MANIFEST-")


def _source_rule_errors(result: ValidationResult) -> list:
    return [i for i in result.issues
            if i.category == "RULE" and i.severity in {"CRITICAL", "HIGH"}
            and not i.code.startswith(_PROJECT_COMPLETENESS_PREFIXES)]


def _workspace_blob(workspace: Path) -> str:
    """仅拼接「源码」内容用于 acceptance 断言（排除 dist/build 编译产物与 node_modules）。"""
    parts = []
    for p in sorted(workspace.rglob("*")):
        if not p.is_file() or p.suffix not in {".ts", ".js", ".jsx", ".tsx", ".html"}:
            continue
        rel = p.relative_to(workspace)
        if "node_modules" in rel.parts or "dist" in rel.parts or "build" in rel.parts:
            continue
        parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def _evaluate_case(case: BenchmarkCase, evaluator: BenchmarkEvaluator,
                   workspace: Path) -> tuple[bool, ValidationResult, dict]:
    """运行确定性 Evaluator 并派生统一的判定的派生指标。"""
    success, result = evaluator.evaluate(case, workspace)
    source_issues = _source_api_errors(result) + _source_rule_errors(result)
    api_errors = _source_api_errors(result)
    hallucination = any(i.code in HALLUCINATION_CODES for i in api_errors)
    rule_errors = _source_rule_errors(result)
    failure_type = ""
    if not success:
        if hallucination:
            failure_type = "API_HALLUCINATION"
        elif rule_errors:
            failure_type = "RULE_VIOLATION"
        else:
            failure_type = "ACCEPTANCE_FAIL"
    abstained = case.allow_abstention and not api_errors
    derived = {
        "failure_type": failure_type,
        "api_correct": not api_errors,
        "constraint_violation": bool(rule_errors),
        "hallucination": hallucination,
        "abstained": abstained,
        "issues": [i.to_dict() for i in source_issues],
    }
    return success, result, derived


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def load_cases(tasks_json: Path) -> list[BenchmarkCase]:
    """映射真实数据集 schema（见 benchmark/tasks.json meta.field_notes）。"""
    data = json.loads(tasks_json.read_text(encoding="utf-8"))
    cases: list[BenchmarkCase] = []
    for raw in data.get("tasks", []):
        acceptance = raw.get("acceptance", {}) or {}
        tests = [t for t in acceptance.get("tests", []) if isinstance(t, str)]
        forbidden = [t[len("not_contains:"):] for t in tests if t.startswith("not_contains:")]
        ref_symbols = raw.get("reference_symbols", []) or []
        category = raw.get("category", "general")
        cases.append(BenchmarkCase(
            id=raw["id"],
            name=raw.get("title", raw["id"]),
            category=category,
            description=raw.get("description", ""),
            user_request=raw.get("task_prompt", ""),
            fixture=str(raw.get("initial_project", "")).replace("fixtures/", "", 1),
            sdk_version=str(raw.get("sdk_version", "") or ""),
            expected_behavior=raw.get("expected_behavior", ""),
            acceptance_tests=tests,
            expected_api_symbols=ref_symbols + forbidden,
            initial_expect_fail=bool(acceptance.get("initial_expect_fail", True)),
            allow_abstention=category in ABSTENTION_CATEGORIES,
        ))
    return cases


def load_tasks_raw(tasks_json: Path) -> dict[str, dict]:
    data = json.loads(tasks_json.read_text(encoding="utf-8"))
    return {t["id"]: t for t in data.get("tasks", [])}


def run_case(case: BenchmarkCase, driver, mode: str,
             evaluator: BenchmarkEvaluator, run_index: int, work_root: Path) -> CaseResult:
    workspace = work_root / mode / f"{case.id}-r{run_index}"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    start = time.time()
    try:
        driver.produce(case, mode, workspace)
        success, result = evaluator.evaluate(case, workspace)
    except NotImplementedError:
        raise
    except Exception as error:  # 真实异常也要如实记录
        latency = (time.time() - start) * 1000
        return CaseResult(case.id, mode, run_index, False, latency,
                          failure_type=f"HARNESS_ERROR:{type(error).__name__}",
                          issues=[{"error": str(error), "trace": traceback.format_exc()[:500]}])
    latency = (time.time() - start) * 1000
    _, _, derived = _evaluate_case(case, evaluator, workspace)
    return CaseResult(
        case_id=case.id, mode=mode, run_index=run_index, success=success, latency_ms=latency,
        failure_type=derived["failure_type"], first_pass=None,
        api_correct=derived["api_correct"],
        constraint_violation=derived["constraint_violation"],
        hallucination=derived["hallucination"],
        abstained=derived["abstained"],
        abstain_expected=case.allow_abstention,
        issues=derived["issues"],
        extra={"first_pass_basis": "unavailable", "first_pass_reason":
               "CodeBuddy trace cannot reliably distinguish first-pass candidate from self-repair"},
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results) or 1
    abstain = [r for r in results if r.abstain_expected]
    # First Pass：仅当所有 run 都能可靠判断时才计算成功率；
    # 任一 run 为 UNAVAILABLE（None）则整体报 UNAVAILABLE，绝不把「未知」当成 0%。
    first_pass_known = [r.first_pass for r in results if r.first_pass is not None]
    first_pass_rate: float | str
    if not first_pass_known:
        first_pass_rate = "UNAVAILABLE"
    else:
        first_pass_rate = round(sum(1 for v in first_pass_known if v) / len(first_pass_known), 4)
    return {
        "task_success_rate": round(sum(r.success for r in results) / n, 4),
        "first_pass_success_rate": first_pass_rate,
        "api_correctness": round(sum(r.api_correct for r in results) / n, 4),
        "constraint_violation_rate": round(sum(r.constraint_violation for r in results) / n, 4),
        "hallucination_rate": round(sum(r.hallucination for r in results) / n, 4),
        "abstention_correct_rate": (
            round(sum(r.success for r in abstain) / len(abstain), 4) if abstain else None
        ),
        "mean_latency_ms": round(sum(r.latency_ms for r in results) / n, 2),
        "tokens": "UNAVAILABLE",
        "count": len(results),
    }


# ---------------------------------------------------------------------------
# Reference Driver（harness 自检）
# ---------------------------------------------------------------------------


def run_reference(args, cases, evaluator, driver, out_dir: Path) -> int:
    modes = ["baseline", "mcp"] if args.mode == "both" else [args.mode]
    all_results: list[CaseResult] = []
    per_mode: dict[str, list[CaseResult]] = {m: [] for m in modes}

    for mode in modes:
        for case in cases:
            for run_index in range(args.runs):
                res = run_case(case, driver, mode, evaluator, run_index, out_dir / "_runs")
                all_results.append(res)
                per_mode[mode].append(res)

    records = [vars(r) for r in all_results]
    (out_dir / "agent_benchmark_runs.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {m: aggregate(per_mode[m]) for m in modes}
    (out_dir / "agent_benchmark_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_comparison(out_dir / "agent_benchmark_comparison.md", summary, modes,
                      reference=True)
    print("Benchmark 完成（reference driver）。结果：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"逐条结果：{out_dir / 'agent_benchmark_runs.json'}")
    return 0


# ---------------------------------------------------------------------------
# CodeBuddy Manual Driver（真实执行 + 回灌）
# ---------------------------------------------------------------------------


def _mcp_servers_for(args) -> dict:
    """Plugin Dev Helper MCP 的真实接入点（与 CodeBuddy 用户级配置一致：已部署服务）。"""
    url = args.mcp_url or "http://124.223.217.62:8011/mcp"
    return {
        "plugin-dev-helper": {
            "type": "http",
            "url": url,
            "description": "Plugin Developer MCP (Streamable HTTP): SDK/API 检索与校验、插件 Guardrail 约束、脚手架",
        }
    }


def run_codebuddy_prepare(args, cases, driver: CodeBuddyManualDriver, out_dir: Path) -> int:
    modes = ["baseline", "mcp"] if args.mode == "both" else [args.mode]
    manifest: list[dict] = []
    ws_root = out_dir / "workspaces"
    for mode in modes:
        for case in cases:
            for run_index in range(args.runs):
                workspace = ws_root / mode / f"{case.id}-r{run_index}"
                if workspace.exists():
                    shutil.rmtree(workspace)
                workspace.mkdir(parents=True)
                res = driver.prepare_run(case, mode, workspace)
                manifest.append({
                    "task_id": case.id,
                    "title": case.name,
                    "category": case.category,
                    "mode": mode,
                    "run_index": run_index,
                    # 持久化使用相对仓库根的路径，不暴露本机绝对路径
                    "workspace": str(workspace.relative_to(ROOT)) if _is_relative_to(workspace, ROOT) else str(workspace),
                    "prompt_path": _rel_if_possible(res.metadata.get("prompt_path"), ROOT),
                    "mcp_enabled": bool(res.metadata.get("mcp_enabled")),
                    "user_request": case.user_request,
                })
    (out_dir / "runs_manifest.json").write_text(
        json.dumps({
            "driver": "codebuddy-manual",
            "agent": driver.config.agent,
            "model": driver.config.model,
            "runs_per_task": args.runs,
            "modes": modes,
            "task_count": len(cases),
            "mcp_servers": list(driver.config.mcp_servers.keys()),
            "entries": manifest,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[prepare] 已准备 {len(manifest)} 个工作区（{len(cases)} 任务 × "
          f"{args.runs} runs × {len(modes)} 条件）于 {ws_root}")
    print("[prepare] 请在真实 CodeBuddy 中逐一打开 workspace，完成任务后保存 vm.ts；")
    print("          MCP 条件工作区已写入 .codebuddy/mcp.json；baseline 工作区不含 MCP 配置。")
    print(f"[prepare] 完成后运行：python scripts/run_coding_agent_benchmark.py "
          f"--driver codebuddy-manual --import --out-dir {out_dir}")
    return 0


def run_codebuddy_import(args, cases, evaluator, driver: CodeBuddyManualDriver,
                         out_dir: Path) -> int:
    manifest_path = out_dir / "runs_manifest.json"
    if not manifest_path.exists():
        print("[import] 未找到 runs_manifest.json，请先运行 --prepare。")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases_by_id = {c.id: c for c in cases}
    tasks_raw = load_tasks_raw(Path(args.tasks_json))

    run_id = f"codebuddy-{_stamp()}"
    ts_dir = Path(args.benchmark_dir) / "traces" / run_id
    ts_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[CaseResult] = []
    per_mode: dict[str, list[CaseResult]] = {"baseline": [], "mcp": []}
    trace_payloads: list[dict] = []
    trace_payloads_baseline: list[dict] = []

    for entry in manifest["entries"]:
        case = cases_by_id.get(entry["task_id"])
        if case is None:
            continue
        workspace = Path(entry["workspace"])
        if not workspace.is_absolute():
            workspace = ROOT / workspace
        # 隔离校验：验证 baseline 工作区确实未启用 MCP，mcp 工作区确实启用。
        isolation = verify_isolation(entry, workspace, ROOT)
        # 读取 trace（若真实 Agent 已回灌）
        trace_raw = None
        trace_file = workspace / "trace.json"
        if trace_file.exists():
            try:
                trace_raw = json.loads(trace_file.read_text(encoding="utf-8"))
            except Exception:
                trace_raw = None
        success, result, derived = _evaluate_case(case, evaluator, workspace)

        trace_metrics: dict = {}
        condition = "with_mcp" if entry["mode"] == "mcp" else "without_mcp"
        if trace_raw:
            norm = normalize_codebuddy_trace(
                trace_raw, run_id=run_id, agent=driver.config.agent, condition=condition)
            raw_task = tasks_raw.get(case.id, {})
            # 组合成单次 run 的 trace，供 check_agent_trace 评分
            single = {"run_id": f"{run_id}-{entry['mode']}",
                      "agent": driver.config.agent, "condition": condition, "tasks": []}
            # 把该 task 的调用并入 single trace（按 task_id 匹配）
            for t in norm.get("tasks", []):
                if t.get("task_id") == case.id:
                    single["tasks"].append(t)
            target = trace_payloads if entry["mode"] == "mcp" else trace_payloads_baseline
            target.append(single)
            # 直接本任务评分
            if raw_task:
                from scripts.check_agent_trace import score_task
                trace_metrics = score_task(raw_task, single["tasks"][0] if single["tasks"] else {})

        # baseline 隔离被破坏（工作区含 MCP 配置）→ 该 run 不能计入正式 Baseline
        environment_invalid = bool(
            entry["mode"] != "mcp" and isolation.get("isolation_verified") is False
        )
        res = CaseResult(
            case_id=case.id, mode=entry["mode"], run_index=entry["run_index"],
            success=success, latency_ms=0.0,
            failure_type=derived["failure_type"], first_pass=None,
            api_correct=derived["api_correct"],
            constraint_violation=derived["constraint_violation"],
            hallucination=derived["hallucination"],
            abstained=derived["abstained"],
            abstain_expected=case.allow_abstention,
            issues=derived["issues"],
            trace_metrics=trace_metrics,
            extra={
                "first_pass_basis": "unavailable",
                "first_pass_reason":
                    "CodeBuddy trace cannot reliably distinguish first-pass candidate from self-repair",
                "mcp_enabled": bool(entry["mcp_enabled"]),
                "manual_execution": True,
                "workspace": str(workspace.relative_to(ROOT)) if _is_relative_to(workspace, ROOT) else str(workspace),
                "has_trace": bool(trace_raw),
                "isolation": isolation,
                "environment_invalid": environment_invalid,
            },
        )
        all_results.append(res)
        # 仅当环境隔离有效时才纳入该模式的正式聚合
        if entry["mode"] in per_mode and not environment_invalid:
            per_mode[entry["mode"]].append(res)
        elif entry["mode"] in per_mode and environment_invalid:
            # 仍保留 baseline 数据但标记，便于审计；不计入 success 聚合
            pass

    # 落盘真实结果
    result_dir = out_dir / run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "raw_results.json").write_text(
        json.dumps([vars(r) for r in all_results], ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {m: aggregate(per_mode[m]) for m in per_mode if per_mode[m]}
    # manual / import 模式无法采集真实 Agent 延迟；标记 UNAVAILABLE，不用 0 冒充。
    for _m in summary:
        summary[_m]["mean_latency_ms"] = "UNAVAILABLE"
    (result_dir / "aggregate.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata = _build_metadata(args, driver, manifest, run_id, cases, summary,
                               results=all_results)
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_real_comparison(result_dir / "comparison.md", all_results, per_mode, summary, metadata)

    # 真实 trace 落盘（canonical schema）
    if trace_payloads:
        (ts_dir / "with_mcp.json").write_text(
            json.dumps({"runs": trace_payloads}, ensure_ascii=False, indent=2), encoding="utf-8")
    if trace_payloads_baseline:
        (ts_dir / "without_mcp.json").write_text(
            json.dumps({"runs": trace_payloads_baseline}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 用统一评分器产出 agent_trace_results.json（若至少有一条 trace）
    if trace_payloads or trace_payloads_baseline:
        _run_trace_scorer(ts_dir, result_dir)

    print(f"[import] 真实 CodeBuddy Benchmark 完成：{result_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _run_trace_scorer(ts_dir: Path, result_dir: Path) -> None:
    try:
        from scripts.check_agent_trace import score_run
        payloads: list[dict] = []
        for f in ("with_mcp.json", "without_mcp.json"):
            p = ts_dir / f
            if p.exists():
                payloads.extend(json.loads(p.read_text(encoding="utf-8")).get("runs", []))
        if not payloads:
            return
        scored = [score_run(pl) for pl in payloads]
        (result_dir / "agent_trace_results.json").write_text(
            json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:  # 评分失败不阻断主流程
        print(f"[import] trace 评分跳过：{error}")


def _build_metadata(args, driver, manifest, run_id, cases, summary, results=None) -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        commit = "UNAVAILABLE"
    # 隔离汇总：统计 baseline / mcp 的隔离校验结果
    isolation_summary = _summarize_isolation(manifest)
    if results is not None:
        invalid = [r for r in results if r.extra.get("environment_invalid")]
        isolation_summary["invalid_environment_runs"] = len(invalid)
        isolation_summary["excluded_modes"] = sorted({r.mode for r in invalid})
    isolation_summary["modes_reported"] = sorted(summary.keys())
    return {
        "agent": driver.config.agent,
        "agent_version": "CodeBuddy CN 1.106.1",
        "model": driver.config.model,
        "driver": "codebuddy-manual",
        "manual_execution": True,
        "benchmark_version": "0.3.0",
        "git_commit": commit,
        "started_at": _now(),
        "finished_at": _now(),
        "runs_per_task": args.runs,
        "task_count": len(cases),
        "baseline_config": {"mcp_enabled": False,
                            "note": "工作区不写入 .codebuddy/mcp.json；若 CodeBuddy 合并用户级 mcp.json 提供 plugin-dev-helper，则该环境变量无法完全固定（见 ADR-004）"},
        "mcp_config": {"mcp_enabled": True,
                       "mcp_servers": list(driver.config.mcp_servers.keys()),
                       "url": list(driver.config.mcp_servers.values())[0]["url"] if driver.config.mcp_servers else ""},
        "isolation": isolation_summary,
        "environment": {
            "os": "darwin",
            "known_limitations": [
                "fixture 为最小 TypeScript 工程，不等价于真实宿主插件运行环境",
                "CodeBuddy 模型可能具有随机性；本执行 runs 样本量小，不声称统计稳定",
                "token / cost 无法从 CodeBuddy 程序化获取，标记 UNAVAILABLE",
                "baseline 是否真正禁用了全局 MCP 取决于 CodeBuddy 配置合并行为（已披露）",
                "First Pass Success 因 CodeBuddy trace 无法区分首次候选实现与自修复，标记 UNAVAILABLE",
            ],
        },
        "summary": summary,
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _rel_if_possible(path: str | None, root: Path) -> str | None:
    if not path:
        return path
    p = Path(path)
    if p.is_absolute() and _is_relative_to(p, root):
        return str(p.relative_to(root))
    return str(p)


def verify_isolation(entry: dict, workspace: Path, root: Path) -> dict:
    """Preflight / post-hoc 隔离校验。

    baseline 模式：期望工作区不含 .codebuddy/mcp.json；
    mcp 模式：期望工作区含 .codebuddy/mcp.json。

    返回 mode / mcp_expected / mcp_detected / isolation_verified。
    若工作区文件不存在（无法检查），isolation_verified = UNAVAILABLE。
    """
    mode = entry.get("mode", "baseline")
    mcp_expected = mode == "mcp"
    mcp_json = workspace / ".codebuddy" / "mcp.json"
    if not workspace.exists():
        return {
            "mode": mode, "mcp_expected": mcp_expected,
            "mcp_detected": "UNAVAILABLE", "isolation_verified": "UNAVAILABLE",
            "note": "workspace not found on disk; cannot verify isolation",
        }
    mcp_detected = bool(mcp_json.exists())
    verified = (mcp_detected == mcp_expected)
    return {
        "mode": mode, "mcp_expected": mcp_expected,
        "mcp_detected": mcp_detected, "isolation_verified": verified,
    }


def _summarize_isolation(manifest: dict) -> dict:
    entries = manifest.get("entries", [])
    baseline = [e for e in entries if e.get("mode") != "mcp"]
    mcp = [e for e in entries if e.get("mode") == "mcp"]
    # 隔离校验在 import 时重算；此处仅做轻量计数（import 会把 isolation 写进 extra）。
    return {
        "method": "workspace .codebuddy/mcp.json presence check",
        "baseline_workspaces": len(baseline),
        "mcp_workspaces": len(mcp),
        "note": "per-run isolation_verified 见各 result.extra.isolation；"
                "baseline 若检测到 MCP 配置则该 run 不计入正式 Baseline（INVALID_ENVIRONMENT）。",
    }


# ---------------------------------------------------------------------------
# CodeBuddy CLI Driver（真实调用；本环境 headless 采集不可用 -> NOT_RUN）
# ---------------------------------------------------------------------------


def run_codebuddy_cli(args, driver: CodeBuddyCLIDriver, out_dir: Path) -> int:
    """真实 CLI 调用。本环境 `buddycn chat` 不向 stdout 返回结果，无法 headless 采集，
    故直接判 NOT_RUN 并说明原因，不伪造任何 Agent 输出。"""
    (out_dir / "codebuddy_cli_not_run.json").write_text(json.dumps({
        "status": "NOT_RUN",
        "reason": "CodeBuddy `chat` CLI 在本环境不向 stdout 返回结果/轨迹（仅拉起 GUI），"
                  "无法程序化采集 Agent 响应与 tool trace。请改用 --driver codebuddy-manual。",
        "modes": ["baseline", "mcp"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("NOT_RUN: CodeBuddy CLI 无法 headless 采集结果（见 codebuddy_cli_not_run.json）。"
          "请改用 --driver codebuddy-manual 执行真实 Benchmark。")
    return 0


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def _write_comparison(path: Path, summary: dict, modes: list[str], reference: bool = False) -> None:
    metrics = [
        ("Task Success", "task_success_rate"),
        ("First Pass", "first_pass_success_rate"),
        ("API Correctness", "api_correctness"),
        ("Constraint Violation", "constraint_violation_rate"),
        ("Hallucination", "hallucination_rate"),
        ("Latency(ms)", "mean_latency_ms"),
        ("Token", "tokens"),
    ]
    lines = ["# Coding Agent Benchmark: Baseline vs MCP", "",
             "> 控制变量：唯一差异为是否访问 plugin-dev-helper MCP。"]
    if reference:
        lines += [
            "> 本结果为 **reference driver（确定性 stand-in）** 的真实校验/评分结果，仅代表 harness 自检。",
            "> 真实 CodeBuddy Baseline vs MCP 实验见 `benchmark/results/codebuddy-*/comparison.md`"
            "（`codebuddy-manual` 驱动，manual_execution=true）。", ""]
    header = "| Metric | " + " | ".join(m.upper() for m in modes) + " |"
    if "baseline" in modes and "mcp" in modes:
        header += " Delta |"
    lines.append(header)
    lines.append("|" + "---:|" * (len(modes) + (1 if len(modes) == 2 else 0)))
    for label, key in metrics:
        row = f"| {label} |"
        vals = []
        for m in modes:
            v = summary[m].get(key, "")
            vals.append(f" {v} ")
            row += f" {v} |"
        if "baseline" in modes and "mcp" in modes:
            b, mc = summary["baseline"].get(key), summary["mcp"].get(key)
            if isinstance(b, (int, float)) and isinstance(mc, (int, float)):
                delta = round(mc - b, 4)
                row += f" {delta} |"
            else:
                row += " - |"
        lines.append(row)
    lines.append("")
    if reference:
        lines.append("**说明：reference driver 仅为 harness 自检，不是 Agent 能力分数。**")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_real_comparison(path: Path, all_results: list[CaseResult],
                           per_mode: dict, summary: dict, metadata: dict) -> None:
    lines = ["# CodeBuddy Benchmark: Baseline vs MCP（真实执行）", "",
             f"> Agent: {metadata.get('agent')}  |  Model: {metadata.get('model')}  |  "
             f"Driver: codebuddy-manual (manual_execution=true)",
             f"> Runs/task: {metadata.get('runs_per_task')}  |  Tasks: {metadata.get('task_count')}  |  "
             f"Commit: {metadata.get('git_commit')}",
             "> 控制变量：唯一差异为是否访问 plugin-dev-helper MCP。",
             "> Token/Cost 无法从 CodeBuddy 程序化获取，标记 UNAVAILABLE。", ""]
    lines += [
        "## Experiment",
        "",
        "- 目的：定量验证 Coding Agent（CodeBuddy）接入 Plugin Dev Helper MCP 后在真实插件 SDK 开发任务上的行为变化。",
        "- 实验设计：同一 CodeBuddy、同一 task prompt、同一初始 fixture，唯一变量为是否可访问 plugin-dev-helper MCP。",
        "- 控制变量：Agent / 任务集 / fixture / evaluator / validator / acceptance 全部固定；模型元数据 CodeBuddy 未提供，标记 UNAVAILABLE。",
        "- CodeBuddy 环境：CLI 存在但无 headless 采集接口；本实验以 manual/import 驱动执行（manual_execution=true）。",
        f"- 任务数量：{metadata.get('task_count')}；重复次数：{metadata.get('runs_per_task')} run/task。",
        "- Evaluator：ProjectValidator（源码 API/规则确定性校验）+ acceptance 字符串断言；不使用 LLM Judge。",
        f"- MCP 接入点：{metadata.get('mcp_config', {}).get('url', '')}（已部署 Plugin Dev Helper MCP）。",
        "- Baseline 的 MCP：工作区不写入 .codebuddy/mcp.json，无 MCP trace。",
        "",
    ]
    # 汇总表
    lines.append("## 汇总")
    lines.append("")
    lines.append("| Metric | Baseline | MCP | Delta |")
    lines.append("|---:|---:|---:|---:|")
    metric_pairs = [
        ("Task Success Rate", "task_success_rate"),
        ("First Pass Success Rate", "first_pass_success_rate"),
        ("API Correctness", "api_correctness"),
        ("Constraint Violation Rate", "constraint_violation_rate"),
        ("Hallucination Rate", "hallucination_rate"),
        ("Abstention Correct Rate", "abstention_correct_rate"),
        ("Mean Latency (ms)", "mean_latency_ms"),
        ("Token", "tokens"),
    ]
    for label, key in metric_pairs:
        b = summary.get("baseline", {}).get(key, "UNAVAILABLE")
        mc = summary.get("mcp", {}).get(key, "UNAVAILABLE")
        if isinstance(b, (int, float)) and isinstance(mc, (int, float)):
            delta = round(mc - b, 4)
        else:
            delta = "-"
        lines.append(f"| {label} | {b} | {mc} | {delta} |")
    lines.append("")

    # First Pass 说明（UNAVAILABLE 的语义解释）
    lines.append("> **First Pass Success Rate = UNAVAILABLE**：当前 CodeBuddy trace 无法可靠区分")
    lines.append("> 「首次候选实现」与「后续自修复」，因此不把 Final Success 当作 First Pass，")
    lines.append("> 也不伪造该指标。详见 § 已知限制。")
    lines.append("")

    # Per-task
    lines.append("## Per-task")
    lines.append("")
    lines.append("| Task | Mode | Success | Failure Type | Abstained | Trace F1 | Symbol Cov |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in all_results:
        tm = r.trace_metrics or {}
        f1 = tm.get("tool_selection", {}).get("f1", "-") if tm else "-"
        cov = tm.get("symbol_coverage", "-") if tm else "-"
        lines.append(f"| {r.case_id} | {r.mode} | {r.success} | {r.failure_type or '-'} | "
                     f"{r.abstained} | {f1} | {cov} |")
    lines.append("")

    # MCP 使用行为（仅 MCP 条件存在真实 trace）
    mcp_traces = [r.trace_metrics for r in all_results if r.mode == "mcp" and r.trace_metrics]
    lines.append("## MCP 使用行为（仅 MCP 条件）")
    lines.append("")
    if mcp_traces:
        def _avg(path_keys):
            vals = []
            for tm in mcp_traces:
                cur = tm
                for k in path_keys:
                    cur = cur.get(k, {}) if isinstance(cur, dict) else None
                    if cur is None:
                        break
                if isinstance(cur, (int, float)):
                    vals.append(cur)
            return round(sum(vals) / len(vals), 4) if vals else "N/A"

        lines.append("| Metric | MCP | Baseline |")
        lines.append("|---|---|---|")
        lines.append(f"| Tool Selection Precision | {_avg(['tool_selection', 'precision'])} | N/A |")
        lines.append(f"| Tool Selection Recall | {_avg(['tool_selection', 'recall'])} | N/A |")
        lines.append(f"| Tool Selection F1 | {_avg(['tool_selection', 'f1'])} | N/A |")
        lines.append(f"| Symbol Coverage | {_avg(['symbol_coverage'])} | N/A |")
        lines.append(f"| Redundant Call Rate | {_avg(['redundant_call_rate'])} | N/A |")
        lines.append(f"| Sequence Compliance | {_avg(['sequence', 'rate'])} | N/A |")
        lines.append(f"| Avg MCP Calls / Task | {_avg(['call_count'])} | N/A |")
    else:
        lines.append("- 无 MCP trace（NOT_RUN）")
    lines.append("")

    # Failure analysis
    lines.append("## Failure Analysis")
    lines.append("")
    fail_types: dict[str, int] = {}
    for r in all_results:
        if not r.success and r.failure_type:
            fail_types[r.failure_type] = fail_types.get(r.failure_type, 0) + 1
    if fail_types:
        for ft, cnt in sorted(fail_types.items(), key=lambda x: -x[1]):
            lines.append(f"- {ft}: {cnt}")
    else:
        lines.append("- 无失败（全部通过 acceptance + 确定性校验）")
    lines.append("")

    # Limitations
    lines.append("## Limitations")
    lines.append("")
    for lim in metadata.get("environment", {}).get("known_limitations", []):
        lines.append(f"- {lim}")
    lines.append("")
    lines.append("**真实 Coding Agent 执行状态：EXECUTED（manual / import）**")
    path.write_text("\n".join(lines), encoding="utf-8")


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Coding Agent Benchmark (Baseline vs MCP)")
    parser.add_argument("--mode", choices=["baseline", "mcp", "both"], default="both")
    parser.add_argument("--driver", choices=["reference", "codebuddy-manual", "codebuddy-cli"],
                        default="reference")
    parser.add_argument("--endpoint", default=None, help="(已废弃) 旧 external driver 参数；请使用 codebuddy-manual")
    parser.add_argument("--mcp-url", default=None,
                        help="Plugin Dev Helper MCP 接入点（默认已部署服务 http://124.223.217.62:8011/mcp）")
    parser.add_argument("--tasks-json", default=str(ROOT / "benchmark" / "tasks.json"))
    parser.add_argument("--benchmark-dir", default=str(ROOT / "benchmark"))
    parser.add_argument("--runs", type=int, default=1, help="每个 case 重复次数（默认 1）")
    parser.add_argument("--out-dir", default=str(ROOT / "benchmark" / "results"))
    parser.add_argument("--prepare", action="store_true", help="codebuddy-manual: 准备工作区")
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="codebuddy-manual: 回灌并执行评估")
    args = parser.parse_args()

    benchmark_dir = Path(args.benchmark_dir)
    tasks_json = Path(args.tasks_json)
    cases = load_cases(tasks_json)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.driver == "reference":
        container = build_container(replace(get_mcp_settings(), telemetry_enabled=False))
        evaluator = BenchmarkEvaluator(container.project_validator)
        driver = ReferenceDriver(benchmark_dir)
        return run_reference(args, cases, evaluator, driver, out_dir)

    if args.driver == "codebuddy-cli":
        config = CodeBuddyConfig(
            agent="codebuddy", mode="cli", mcp_enabled=True,
            mcp_servers=_mcp_servers_for(args), runs=args.runs)
        driver = CodeBuddyCLIDriver(benchmark_dir, config)
        return run_codebuddy_cli(args, driver, out_dir)

    # codebuddy-manual
    config = CodeBuddyConfig(
        agent="codebuddy", model="UNAVAILABLE", mode="manual", mcp_enabled=True,
        mcp_servers=_mcp_servers_for(args), runs=args.runs)
    driver = CodeBuddyManualDriver(benchmark_dir, config)
    if args.prepare:
        return run_codebuddy_prepare(args, cases, driver, out_dir)
    if args.do_import:
        container = build_container(replace(get_mcp_settings(), telemetry_enabled=False))
        evaluator = BenchmarkEvaluator(container.project_validator)
        return run_codebuddy_import(args, cases, evaluator, driver, out_dir)
    print("codebuddy-manual 需要 --prepare 或 --import。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
