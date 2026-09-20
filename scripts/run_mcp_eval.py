"""MCP 工具级黄金评测执行器。

针对 benchmark/mcp_golden.json 对 8 个 MCP 工具做确定性断言，
不调用 LLM、不需要 Coding Agent，可直接在 CI 中运行。

指标：
  L0 契约  Contract Pass Rate / Status Accuracy / Determinism Rate
  L1 质量  Not-Found Precision / Citation Completeness / Task Symbol Coverage
  L2 性能  每个工具的平均与 P95 耗时（来自 payload.duration_ms）

用法：
  python scripts/run_mcp_eval.py
  python scripts/run_mcp_eval.py --repeat 5
  python scripts/run_mcp_eval.py --case MCP-A07
  python scripts/run_mcp_eval.py --json benchmark/results/mcp_eval_results.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN_FILE = ROOT / "benchmark" / "mcp_golden.json"
TASKS_FILE = ROOT / "benchmark" / "tasks.json"
GATE_FILE = ROOT / "benchmark" / "mcp_gate.json"
DEFAULT_REPORT = ROOT / "benchmark" / "results" / "mcp_eval_results.json"

VOLATILE_FIELDS = ("request_id", "duration_ms", "tool")


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<MISSING>"


MISSING = _Missing()


def resolve(payload: Any, path: str) -> Any:
    """按点号路径取值，支持 list 下标与 '[*]' 展开。

    返回单个值（唯一命中）或列表（存在 '[*]' 展开）。未命中返回 MISSING。
    """
    # 允许内联通配写法：results[*].symbol 等价于 results.[*].symbol
    normalized = path.replace("[*]", ".[*].")
    cursor: list[Any] = [payload]
    for segment in [seg for seg in normalized.split(".") if seg]:
        nxt: list[Any] = []
        if segment == "[*]":
            for item in cursor:
                if isinstance(item, list):
                    nxt.extend(item)
            cursor = nxt
            continue
        for item in cursor:
            if isinstance(item, dict) and segment in item:
                nxt.append(item[segment])
            elif isinstance(item, list) and segment.lstrip("-").isdigit():
                index = int(segment)
                if -len(item) <= index < len(item):
                    nxt.append(item[index])
        cursor = nxt
    if not cursor:
        return MISSING
    if len(cursor) == 1:
        return cursor[0]
    return cursor


def _is_empty(value: Any) -> bool:
    return value is MISSING or value is None or value in ("", [], {})


def _as_list(value: Any) -> list[Any]:
    if value is MISSING:
        return []
    if isinstance(value, list):
        return value
    return [value]


def check_assertion(name: str, payload: dict, expect: dict) -> list[str]:
    """返回失败原因列表；为空表示通过。"""
    failures: list[str] = []

    # ---- 契约：字段存在且非空 ----
    for path in expect.get("truthy", []):
        if _is_empty(resolve(payload, path)):
            failures.append(f"truthy 失败: {path} 缺失或为空")

    for path in expect.get("falsy", []):
        if not _is_empty(resolve(payload, path)):
            failures.append(f"falsy 失败: {path} 应为空，实际 {resolve(payload, path)!r}")

    # ---- 契约：精确相等 ----
    for path, expected in expect.get("equals", {}).items():
        actual = resolve(payload, path)
        if actual != expected:
            failures.append(f"equals 失败: {path} 期望 {expected!r} 实际 {actual!r}")

    # ---- 状态 ----
    if "status" in expect:
        actual = payload.get("status")
        if actual != expect["status"]:
            failures.append(f"status 失败: 期望 {expect['status']!r} 实际 {actual!r}")

    # ---- 集合长度 ----
    for path, minimum in expect.get("min_len", {}).items():
        value = resolve(payload, path)
        if value is MISSING or len(value) < minimum:
            failures.append(f"min_len 失败: {path} 期望 >= {minimum} 实际 {len(value) if value is not MISSING else 'MISSING'}")

    for path, maximum in expect.get("max_len", {}).items():
        value = resolve(payload, path)
        size = 0 if value is MISSING else len(value)
        if size > maximum:
            failures.append(f"max_len 失败: {path} 期望 <= {maximum} 实际 {size}")

    # ---- 集合成员 ----
    for path, expected_values in expect.get("contains_any", {}).items():
        actual_values = _as_list(resolve(payload, path))
        if not any(item in actual_values for item in expected_values):
            failures.append(f"contains_any 失败: {path} 未命中任一 {expected_values}，实际 {actual_values!r}")

    for path, expected_values in expect.get("contains_all", {}).items():
        actual_values = _as_list(resolve(payload, path))
        missing = [item for item in expected_values if item not in actual_values]
        if missing:
            failures.append(f"contains_all 失败: {path} 缺少 {missing}，实际 {actual_values!r}")

    for path, expected_value in expect.get("all_equal", {}).items():
        actual_values = _as_list(resolve(payload, path))
        if not actual_values:
            failures.append(f"all_equal 失败: {path} 为空，无法校验")
        elif any(item != expected_value for item in actual_values):
            failures.append(f"all_equal 失败: {path} 存在不等于 {expected_value!r} 的项: {actual_values!r}")

    for path, allowed in expect.get("all_in", {}).items():
        actual_values = _as_list(resolve(payload, path))
        outside = [item for item in actual_values if item not in allowed]
        if outside:
            failures.append(f"all_in 失败: {path} 出现不在 {allowed} 中的项: {outside!r}")

    # ---- 子串 ----
    for path, needle in expect.get("contains", {}).items():
        actual = resolve(payload, path)
        if actual is MISSING or needle not in str(actual):
            failures.append(f"contains 失败: {path} 未包含 {needle!r}，实际 {actual!r}")

    for path, needle in expect.get("not_contains", {}).items():
        actual = resolve(payload, path)
        if actual is not MISSING and needle in str(actual):
            failures.append(f"not_contains 失败: {path} 包含了 {needle!r}")

    # ---- 引用可追溯性 ----
    if expect.get("source_lines_valid"):
        start = resolve(payload, "source_lines.start")
        if not isinstance(start, int) or start <= 0:
            failures.append(f"source_lines_valid 失败: source_lines.start={start!r}")

    if expect.get("citation_complete"):
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            failures.append("citation_complete 失败: results 为空")
        else:
            for index, item in enumerate(results):
                if not isinstance(item, dict):
                    failures.append(f"citation_complete 失败: results[{index}] 非对象")
                    continue
                source = item.get("source_file") or item.get("source")
                lines = item.get("source_lines", {}) or {}
                if not source or not isinstance(lines.get("start"), int) or lines["start"] <= 0:
                    failures.append(f"citation_complete 失败: results[{index}] 缺少 source 或行号")
                elif item.get("type") != "document" and not item.get("sdk_version"):
                    failures.append(f"citation_complete 失败: results[{index}] 缺少 sdk_version")

    return failures


def stable_payload(payload: dict) -> str:
    """剔除易变字段后序列化，用于幂等性比对。"""
    clean = {k: v for k, v in payload.items() if k not in VOLATILE_FIELDS}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True)


class McpEvaluator:
    def __init__(self, repeat: int = 3):
        self.repeat = max(1, repeat)
        from mcp_server.config import get_mcp_settings
        from mcp_server.runtime import build_container
        from mcp_server.server import create_mcp_server

        self.settings = get_mcp_settings()
        self.container = build_container(self.settings)
        # 预热 embedding，避免首个 search_docs 调用把模型加载时间计入延迟指标
        try:
            self.container.retrieval.warmup()
        except Exception as error:  # 预热失败不阻断评测
            print(f"[warn] embedding warmup skipped: {error}")
        self.mcp = create_mcp_server(self.settings, self.container)

    # ---------- 调用 ----------

    async def _call(self, tool: str, arguments: dict) -> tuple[str, dict]:
        """返回 (outcome, payload)；outcome 为 responded / rejected。"""
        try:
            result = await self.mcp.call_tool(tool, arguments)
        except Exception as error:  # pydantic 入参校验失败 -> 工具未被调用
            return "rejected", {"__error__": f"{type(error).__name__}: {error}"}
        if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], dict):
            return "responded", result[1]
        contents = result[0] if isinstance(result, tuple) else result
        for content in contents or []:
            text = getattr(content, "text", "")
            if text:
                try:
                    return "responded", json.loads(text)
                except json.JSONDecodeError:
                    return "responded", {"raw": text[:300]}
        return "responded", {}

    @staticmethod
    def _patch(container, dotted: str, replacement):
        """把 container 上的属性替换为 replacement，返回还原函数。"""
        *parents, leaf = dotted.split(".")
        target = container
        for part in parents:
            target = getattr(target, part)
        original = getattr(target, leaf)

        def restore():
            setattr(target, leaf, original)

        setattr(target, leaf, replacement)
        return restore

    def _apply_force(self, arguments: dict, force: dict) -> tuple[dict, list]:
        args = dict(arguments)
        restores: list = []
        if force.get("retrieval_available") is False:
            restores.append(self._patch(self.container, "retrieval._available", False))
        pad_to = force.get("code_pad_to")
        if pad_to and "code" in args:
            base = args["code"]
            if len(base) < pad_to:
                args["code"] = base + "// p\n" * ((pad_to - len(base)) // 6 + 1)
        return args, restores

    # ---------- 单条用例 ----------

    async def run_case(self, case: dict) -> dict:
        tool = case["tool"]
        expect = case.get("expect", {})
        force = case.get("force", {}) or {}
        injection = case.get("inject_error") or {}
        arguments, restores = self._apply_force(case.get("arguments", {}), force)

        if injection:
            def boom(*_args, **_kwargs):
                raise RuntimeError("injected failure for eval")

            restores.append(self._patch(self.container, injection["target"], boom))

        record: dict[str, Any] = {
            "id": case["id"],
            "tool": tool,
            "category": case.get("category", ""),
            "description": case.get("description", ""),
            "passed": False,
            "failures": [],
            "outcome": "",
            "status": "",
            "determinism": None,
            "duration_ms": None,
        }
        try:
            outcome, payload = await self._call(tool, arguments)
            record["outcome"] = outcome
            record["status"] = payload.get("status", "")
            record["duration_ms"] = payload.get("duration_ms")

            if outcome == "rejected":
                if expect.get("outcome") == "rejected":
                    record["passed"] = True
                else:
                    record["failures"].append(f"入参被 MCP 层拒绝（工具未被调用）: {payload.get('__error__', '')[:200]}")
            elif expect.get("outcome") == "rejected":
                record["failures"].append(f"期望被拒绝，实际返回 status={payload.get('status')!r}")
            else:
                record["failures"] = check_assertion(case["id"], payload, expect)

            # 后续步骤（如故障隔离验证）
            then = case.get("then")
            if then:
                then_outcome, then_payload = await self._call(then["tool"], then.get("arguments", {}))
                then_failures = (
                    [f"后续 {then['tool']} 被拒绝，未验证故障隔离"]
                    if then_outcome == "rejected"
                    else check_assertion(f"{case['id']}.then", then_payload, then.get("expect", {}))
                )
                record["failures"].extend(then_failures)
                record["then_status"] = then_payload.get("status", "")

            # 幂等性：同参重复调用应返回一致结果
            if not injection and case.get("determinism", True) and outcome == "responded":
                snapshots = set()
                for _ in range(self.repeat):
                    _, repeated = await self._call(tool, arguments)
                    snapshots.add(stable_payload(repeated))
                record["determinism"] = 1.0 if len(snapshots) == 1 else round(1 / len(snapshots), 4)
                if len(snapshots) > 1:
                    record["failures"].append(
                        f"幂等性失败: {self.repeat} 次调用产生了 {len(snapshots)} 种不同结果"
                    )

            record["passed"] = not record["failures"]
        finally:
            for restore in restores:
                restore()
        return record

    # ---------- 任务符号覆盖 ----------

    async def symbol_coverage(self) -> list[dict]:
        """校验 benchmark 任务的 reference_symbols 是否真的能被 MCP 解析。"""
        tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))["tasks"]
        results = []
        for task in tasks:
            for symbol in task.get("reference_symbols", []):
                _, api_payload = await self._call("get_api", {"symbol": symbol})
                found = bool(api_payload.get("found"))
                resolved_by = "get_api" if found else ""
                if not found:
                    _, type_payload = await self._call("get_type", {"name": symbol})
                    found = bool(type_payload.get("found"))
                    resolved_by = "get_type" if found else ""
                results.append({
                    "task_id": task["id"],
                    "symbol": symbol,
                    "resolved": found,
                    "resolved_by": resolved_by,
                })
        return results


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return round(ordered[index], 2)


def summarize(records: list[dict], coverage: list[dict]) -> dict:
    total = len(records)
    passed = sum(1 for r in records if r["passed"])

    status_cases = [r for r in records if r.get("status")]
    status_expected = [r for r in records if r["outcome"] == "responded"]
    status_ok = sum(1 for r in status_expected if not any(f.startswith("status 失败") for f in r["failures"]))

    determinism_cases = [r for r in records if r.get("determinism") is not None]
    determinism_rate = (
        sum(r["determinism"] for r in determinism_cases) / len(determinism_cases)
        if determinism_cases else 0.0
    )

    not_found_cases = [r for r in records if r["category"] == "not_found"]
    not_found_precision = (
        sum(1 for r in not_found_cases if r["passed"]) / len(not_found_cases)
        if not_found_cases else 0.0
    )

    by_category: dict[str, list[bool]] = {}
    for record in records:
        by_category.setdefault(record["category"], []).append(record["passed"])

    latency: dict[str, list[float]] = {}
    for record in records:
        if isinstance(record.get("duration_ms"), (int, float)):
            latency.setdefault(record["tool"], []).append(float(record["duration_ms"]))

    coverage_rate = (
        sum(1 for item in coverage if item["resolved"]) / len(coverage)
        if coverage else 0.0
    )

    return {
        "total_cases": total,
        "passed_cases": passed,
        "contract_pass_rate": round(passed / total, 4) if total else 0.0,
        "status_accuracy": round(status_ok / len(status_expected), 4) if status_expected else 0.0,
        "determinism_rate": round(determinism_rate, 4),
        "not_found_precision": round(not_found_precision, 4),
        "task_symbol_coverage": round(coverage_rate, 4),
        "by_category": {
            name: {
                "total": len(values),
                "passed": sum(1 for v in values if v),
                "rate": round(sum(1 for v in values if v) / len(values), 4),
            }
            for name, values in sorted(by_category.items())
        },
        "latency_ms": {
            tool: {
                "calls": len(values),
                "avg": round(sum(values) / len(values), 2),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
            }
            for tool, values in sorted(latency.items())
        },
        "status_cases_count": len(status_cases),
    }


def apply_gate(metrics: dict, gate: dict) -> dict:
    checks = []
    mapping = {
        "contract_pass_rate": (metrics["contract_pass_rate"], ">="),
        "status_accuracy": (metrics["status_accuracy"], ">="),
        "determinism_rate": (metrics["determinism_rate"], ">="),
        "not_found_precision": (metrics["not_found_precision"], ">="),
        "task_symbol_coverage": (metrics["task_symbol_coverage"], ">="),
    }
    for key, threshold in gate.items():
        if key not in mapping:
            continue
        actual, _ = mapping[key]
        checks.append({
            "metric": key,
            "actual": actual,
            "threshold": threshold,
            "passed": actual >= threshold,
        })
    return {"checks": checks, "passed": all(c["passed"] for c in checks)}


async def main_async(args: argparse.Namespace) -> int:
    golden = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    cases = golden["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"未找到用例 {args.case}")
            return 2

    evaluator = McpEvaluator(repeat=args.repeat)
    records = []
    for case in cases:
        record = await evaluator.run_case(case)
        records.append(record)
        flag = "PASS" if record["passed"] else "FAIL"
        determinism = "" if record["determinism"] is None else f" det={record['determinism']}"
        print(f"[{flag}] {record['id']} {record['tool']:<24} {record['category']:<12} "
              f"status={record['status'] or '-':<16}{determinism} {record['description'][:36]}")
        for failure in record["failures"]:
            print(f"        - {failure}")

    coverage = [] if args.skip_coverage else await evaluator.symbol_coverage()
    if coverage:
        print("\n-- 任务符号覆盖（benchmark/tasks.json reference_symbols）--")
        for item in coverage:
            flag = "PASS" if item["resolved"] else "FAIL"
            print(f"[{flag}] {item['task_id']} {item['symbol']} -> {item['resolved_by'] or '未解析'}")

    metrics = summarize(records, coverage)
    print("\n" + "=" * 64)
    print("MCP 工具级评测汇总")
    print("=" * 64)
    print(f"  用例总数            : {metrics['total_cases']}")
    print(f"  Contract Pass Rate  : {metrics['contract_pass_rate']:.2%}")
    print(f"  Status Accuracy     : {metrics['status_accuracy']:.2%}")
    print(f"  Determinism Rate    : {metrics['determinism_rate']:.2%}（重复 {args.repeat} 次）")
    print(f"  Not-Found Precision : {metrics['not_found_precision']:.2%}")
    print(f"  Task Symbol Coverage: {metrics['task_symbol_coverage']:.2%}")
    print("\n  分类通过率:")
    for name, stat in metrics["by_category"].items():
        print(f"    {name:<14} {stat['passed']}/{stat['total']} = {stat['rate']:.2%}")
    print("\n  耗时（ms）:")
    for tool, stat in metrics["latency_ms"].items():
        print(f"    {tool:<24} calls={stat['calls']:<3} avg={stat['avg']:<9} p50={stat['p50']:<9} p95={stat['p95']}")

    gate_result = None
    if GATE_FILE.exists():
        gate = json.loads(GATE_FILE.read_text(encoding="utf-8"))
        gate_result = apply_gate(metrics, gate)
        print("\n  门禁:")
        for check in gate_result["checks"]:
            flag = "PASS" if check["passed"] else "FAIL"
            print(f"    [{flag}] {check['metric']:<24} {check['actual']:.2%} >= {check['threshold']:.2%}")
        print(f"  => GATE {'PASS' if gate_result['passed'] else 'FAIL'}")

    report = {
        "repeat": args.repeat,
        "metrics": metrics,
        "gate": gate_result,
        "cases": records,
        "symbol_coverage": coverage,
    }
    out_path = Path(args.json) if args.json else DEFAULT_REPORT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {out_path}")

    failed = metrics["total_cases"] - metrics["passed_cases"]
    if gate_result and not gate_result["passed"]:
        return 1
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP 工具级黄金评测")
    parser.add_argument("--repeat", type=int, default=3, help="幂等性重复调用次数，默认 3")
    parser.add_argument("--case", default=None, help="只跑指定用例 id")
    parser.add_argument("--json", dest="json", default=None, help="报告输出路径")
    parser.add_argument("--skip-coverage", action="store_true", help="跳过任务符号覆盖检查")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
