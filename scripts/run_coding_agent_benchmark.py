"""Coding Agent Benchmark Harness：Baseline vs MCP（确定性评估）。

设计原则（ADR-004）：
- 控制变量：两组实验唯一差异是「是否可访问 plugin-dev-helper MCP」。
- 公平：相同任务、相同初始 fixture、相同 Project Validator、相同 Evaluator。
- 确定性：结果由 Project Validator + acceptance 判定，不依赖 LLM Judge。

Agent Driver：
- ``reference``（默认）：确定性参照驱动，用于验证整套编排与评估管线真实可运行。
  * mcp 模式：在 fixture 基础上应用 reference solution（代表「借助 MCP 正确产出的交付物」）。
  * baseline 模式：直接使用 fixture 原样（代表「无 MCP 时未能修正的交付物」，保守下界）。
  注意：这是真实执行了校验与评分，但「Agent」本身是确定性 stand-in，非真实 LLM Coding Agent。
- ``external``：接入真实外部 Coding Agent（通过 --endpoint）。本仓库未连接真实 Agent，
  执行时该行标记为 NOT_RUN，不产出伪造数据。

真实 Coding Agent Benchmark 的执行状态：NOT_RUN（见 README / benchmark/README.md）。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
    first_pass: bool = False
    api_correct: bool = True
    constraint_violation: bool = False
    hallucination: bool = False
    abstained: bool = False
    abstain_expected: bool = False
    issues: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class BenchmarkDriver:
    """产出 artifact（相对路径 -> 内容）。"""

    def produce(self, case: BenchmarkCase, mode: str, workspace: Path) -> dict[str, str]:
        raise NotImplementedError


class ReferenceDriver(BenchmarkDriver):
    """确定性参照驱动：mcp 应用 solution，baseline 原样 fixture。"""

    def __init__(self, benchmark_dir: Path):
        self.benchmark_dir = benchmark_dir

    def produce(self, case: BenchmarkCase, mode: str, workspace: Path) -> dict[str, str]:
        fixture_dir = self.benchmark_dir / "fixtures" / case.fixture
        solution_dir = self.benchmark_dir / "solutions" / case.id
        # 复制 fixture 到 workspace
        if fixture_dir.exists():
            _copy_tree(fixture_dir, workspace)
        files: dict[str, str] = {}
        for p in sorted(workspace.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(workspace)
            if "node_modules" in rel.parts or "dist" in rel.parts or "build" in rel.parts:
                continue
            files[str(rel)] = p.read_text(encoding="utf-8", errors="ignore")
        if mode == "mcp" and solution_dir.exists():
            self._overlay_solution(files, workspace, solution_dir)
        return files

    @staticmethod
    def _overlay_solution(files: dict[str, str], workspace: Path, solution_dir: Path) -> None:
        for sol in sorted(solution_dir.rglob("*")):
            if not sol.is_file():
                continue
            rel = str(sol.relative_to(solution_dir))
            content = sol.read_text(encoding="utf-8", errors="ignore")
            # 1) 精确相对路径匹配
            target = workspace / rel
            if target.exists():
                target.write_text(content, encoding="utf-8")
                files[rel] = content
                continue
            # 2) basename 匹配（替换 fixture 中同名文件，例如 src/vm.ts <- vm.ts）
            base = sol.name
            match = next((k for k in files if Path(k).name == base), None)
            if match:
                (workspace / match).write_text(content, encoding="utf-8")
                files[match] = content
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                files[rel] = content


class ExternalDriver(BenchmarkDriver):
    """真实外部 Coding Agent 驱动（未连接时抛 NOT_RUN）。"""

    def __init__(self, endpoint: str | None):
        self.endpoint = endpoint

    def produce(self, case: BenchmarkCase, mode: str, workspace: Path) -> dict[str, str]:
        raise NotImplementedError("external driver 未连接真实 Coding Agent（NOT_RUN）")


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


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if "node_modules" in item.parts:
            continue
        relative = item.relative_to(src)
        target = dst / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")


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


def run_case(case: BenchmarkCase, driver: BenchmarkDriver, mode: str,
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
    source_issues = _source_api_errors(result) + _source_rule_errors(result)
    api_errors = _source_api_errors(result)
    # 幻觉口径与成功判定一致：仅统计源文件中的 CRITICAL/HIGH API 错误（不含 dist 残留与 warning）
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
    return CaseResult(
        case_id=case.id, mode=mode, run_index=run_index, success=success, latency_ms=latency,
        failure_type=failure_type, first_pass=success,
        api_correct=not api_errors,
        constraint_violation=bool(rule_errors),
        hallucination=hallucination,
        abstained=abstained,
        abstain_expected=case.allow_abstention,
        issues=[i.to_dict() for i in source_issues],
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results) or 1
    abstain = [r for r in results if r.abstain_expected]
    return {
        "task_success_rate": round(sum(r.success for r in results) / n, 4),
        "first_pass_success_rate": round(sum(r.first_pass for r in results) / n, 4),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Coding Agent Benchmark (Baseline vs MCP)")
    parser.add_argument("--mode", choices=["baseline", "mcp", "both"], default="both")
    parser.add_argument("--driver", choices=["reference", "external"], default="reference")
    parser.add_argument("--endpoint", default=None, help="external driver endpoint (NOT_RUN if unset)")
    parser.add_argument("--tasks-json", default=str(ROOT / "benchmark" / "tasks.json"))
    parser.add_argument("--benchmark-dir", default=str(ROOT / "benchmark"))
    parser.add_argument("--runs", type=int, default=1, help="每个 case 重复次数（默认 1）")
    parser.add_argument("--out-dir", default=str(ROOT / "benchmark" / "results"))
    args = parser.parse_args()

    benchmark_dir = Path(args.benchmark_dir)
    tasks_json = Path(args.tasks_json)
    cases = load_cases(tasks_json)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.driver == "external":
        print("NOT_RUN: external Coding Agent 未连接，请通过 --endpoint 提供真实 Agent。")
        (out_dir / "agent_benchmark_not_run.json").write_text(json.dumps({
            "status": "NOT_RUN",
            "reason": "external Coding Agent 未连接（--driver external 需要 --endpoint）",
            "modes": ["baseline", "mcp"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    # 使用真实知识索引（data/knowledge）与规则层，保证 API/RULE 判定基于线上同一份事实来源；
    # benchmark 自身不写 telemetry 数据库。
    container = build_container(replace(get_mcp_settings(), telemetry_enabled=False))
    evaluator = BenchmarkEvaluator(container.project_validator)
    driver = ReferenceDriver(benchmark_dir)

    modes = ["baseline", "mcp"] if args.mode == "both" else [args.mode]
    all_results: list[CaseResult] = []
    per_mode: dict[str, list[CaseResult]] = {m: [] for m in modes}

    for mode in modes:
        for case in cases:
            for run_index in range(args.runs):
                res = run_case(case, driver, mode, evaluator, run_index, out_dir / "_runs")
                all_results.append(res)
                per_mode[mode].append(res)

    # 持久化逐条结果
    records = [vars(r) for r in all_results]
    (out_dir / "agent_benchmark_runs.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {m: aggregate(per_mode[m]) for m in modes}
    (out_dir / "agent_benchmark_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_comparison(out_dir / "agent_benchmark_comparison.md", summary, modes)
    print("Benchmark 完成（reference driver）。结果：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"逐条结果：{out_dir / 'agent_benchmark_runs.json'}")
    return 0


def _write_comparison(path: Path, summary: dict, modes: list[str]) -> None:
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
             "> 控制变量：唯一差异为是否访问 plugin-dev-helper MCP。",
             "> 本结果为 **reference driver（确定性 stand-in）** 的真实校验/评分结果；",
             "> 真实 LLM Coding Agent Benchmark 状态：**NOT_RUN**（见 README / benchmark/README.md）。", ""]
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
    lines.append("**真实 Coding Agent 执行状态：NOT_RUN**")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
