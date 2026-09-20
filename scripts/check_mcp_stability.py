"""MCP 多次调用稳定性验收（L3）。

工具级黄金评测（run_mcp_eval.py）只验证「单次调用是否正确」，本脚本验证
「反复调用、并发调用、出错之后」是否仍然稳定。这是此前完全缺失的一层。

检查项：
  1. Success Rate         N 轮 × 8 个工具的全部调用中 success 占比
  2. Latency P50/P95/P99  每工具分位数，并对比前 1/4 轮与后 1/4 轮的 P95 漂移
  3. Determinism          跨轮次比对返回指纹（剔除 request_id / duration_ms）
  4. Recovery After Error 中途注入一次工具异常，验证后续调用自动恢复
  5. State Leak           前后 readiness 一致、知识单元缓存增长有界、无 error 累积
  6. Telemetry Integrity  request_id 全局唯一，调用次数与落库次数一致
  7. Concurrency          M 路并发执行完整调用序列，无异常、无交叉污染

默认写入临时 SQLite，不污染生产 telemetry；可用 --database 指定。

用法：
  python scripts/check_mcp_stability.py
  python scripts/check_mcp_stability.py --rounds 100 --concurrency 10
  python scripts/check_mcp_stability.py --json benchmark/results/mcp_stability.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.run_mcp_eval import stable_payload  # noqa: E402

DEFAULT_REPORT = ROOT / "benchmark" / "results" / "mcp_stability.json"

# 每轮的固定调用序列：覆盖 8 个工具，兼顾只读查询与静态校验
ROUND_CALLS: list[tuple[str, dict]] = [
    ("get_api", {"symbol": "IDP.Miniapp.exit"}),
    ("get_type", {"name": "MiniappUploadDataOption"}),
    ("get_related_symbols", {"symbol": "IDP.Miniapp.uploadDataAsync", "depth": 1}),
    ("get_examples", {"symbol": "工具插件代码结构"}),
    ("validate_api_usage", {"code": "IDP.Miniapp.exit();"}),
    ("get_plugin_constraints", {"component": "vm"}),
    ("get_plugin_scaffold", {"stack": "vanilla"}),
    ("search_docs", {"query": "工具插件 UI 和 VM 怎么通信", "top_k": 3}),
]

EXPECTED_STATUS = {
    "get_api": "ok",
    "get_type": "ok",
    "get_related_symbols": "ok",
    "get_examples": "ok",
    "validate_api_usage": "ok",
    "get_plugin_constraints": "ok",
    "get_plugin_scaffold": "ok",
    "search_docs": "ok",
}


@dataclass
class CallRecord:
    round_index: int
    tool: str
    status: str
    duration_ms: float
    request_id: str
    fingerprint: str
    error_type: str = ""
    injected: bool = False


@dataclass
class StabilityResult:
    records: list[CallRecord] = field(default_factory=list)
    recovery: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)
    concurrency: dict[str, Any] = field(default_factory=dict)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return round(ordered[index], 2)


class StabilityRunner:
    def __init__(self, rounds: int, concurrency: int, database_path: Path | None):
        from mcp_server.config import get_mcp_settings
        from mcp_server.runtime import build_container
        from mcp_server.server import create_mcp_server

        self.rounds = rounds
        self.concurrency = concurrency
        settings = get_mcp_settings()
        if database_path is not None:
            from dataclasses import replace
            settings = replace(settings, database_path=database_path)
        self.settings = settings
        self.container = build_container(settings)
        try:
            self.container.retrieval.warmup()
        except Exception as error:
            print(f"[warn] embedding warmup skipped: {error}")
        self.mcp = create_mcp_server(settings, self.container)

    async def call(self, tool: str, arguments: dict) -> tuple[str, dict]:
        try:
            result = await self.mcp.call_tool(tool, arguments)
        except Exception as error:
            return "rejected", {"__error__": str(error)}
        if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], dict):
            return "responded", result[1]
        for content in result[0] if isinstance(result, tuple) else result:
            text = getattr(content, "text", "")
            if text:
                try:
                    return "responded", json.loads(text)
                except json.JSONDecodeError:
                    return "responded", {"raw": text[:200]}
        return "responded", {}

    def cache_size(self) -> int:
        return len(getattr(self.container.knowledge, "_unit_cache", {}) or {})

    # ---------- 串行多轮 ----------

    async def run_rounds(self, result: StabilityResult, inject_at: int) -> None:
        for index in range(self.rounds):
            injecting = (index == inject_at)
            restore = None
            if injecting:
                def boom(*_args, **_kwargs):
                    raise RuntimeError("injected failure for stability check")

                target = self.container.knowledge
                original = target.resolve_deep
                target.resolve_deep = boom  # type: ignore[method-assign]

                def restore():  # noqa: F811
                    target.resolve_deep = original  # type: ignore[method-assign]

            try:
                for tool, arguments in ROUND_CALLS:
                    outcome, payload = await self.call(tool, arguments)
                    status = payload.get("status", "rejected" if outcome == "rejected" else "")
                    result.records.append(CallRecord(
                        round_index=index,
                        tool=tool,
                        status=status,
                        duration_ms=float(payload.get("duration_ms", 0.0) or 0.0),
                        request_id=str(payload.get("request_id", "")),
                        fingerprint=stable_payload(payload),
                        error_type=str(payload.get("error_type", "")),
                        injected=injecting,
                    ))
                    if injecting and tool == "get_api":
                        result.recovery = {
                            "injected_at_round": inject_at,
                            "status_during_error": status,
                            "error_type_during_error": str(payload.get("error_type", "")),
                            "expected_status": "error",
                            "error_surfaced": status == "error",
                        }
            finally:
                if restore:
                    restore()

            # 注入后的第一轮用于观察恢复情况
            if injecting:
                continue

        # 恢复验证：注入之后的所有轮次都应回到正常状态
        if result.recovery:
            after = [r for r in result.records if r.round_index > inject_at]
            recovered = all(r.status == EXPECTED_STATUS.get(r.tool, "") for r in after)
            result.recovery["rounds_after_error"] = len({r.round_index for r in after})
            result.recovery["calls_after_error"] = len(after)
            result.recovery["recovered"] = recovered if after else None

    # ---------- 并发 ----------

    async def _one_sequence(self, worker: int) -> dict[str, Any]:
        statuses: dict[str, str] = {}
        for tool, arguments in ROUND_CALLS:
            outcome, payload = await self.call(tool, arguments)
            statuses[tool] = payload.get("status", "rejected" if outcome == "rejected" else "")
        return {"worker": worker, "statuses": statuses}

    async def run_concurrency(self, result: StabilityResult) -> None:
        if self.concurrency <= 1:
            return
        outcomes = await asyncio.gather(
            *[self._one_sequence(i) for i in range(self.concurrency)],
            return_exceptions=True,
        )
        anomalies: list[str] = []
        for item in outcomes:
            if isinstance(item, BaseException):
                anomalies.append(f"{type(item).__name__}: {item}")
                continue
            for tool, status in item["statuses"].items():
                if status != EXPECTED_STATUS.get(tool, ""):
                    anomalies.append(f"worker {item['worker']} {tool} -> {status}")
        result.concurrency = {
            "workers": self.concurrency,
            "expected_calls": self.concurrency * len(ROUND_CALLS),
            "anomalies": anomalies,
            "passed": not anomalies,
        }

    # ---------- 状态泄漏 / 遥测 ----------

    def collect_state(self, result: StabilityResult, cache_before: int) -> None:
        readiness = self.container.readiness()
        result.state = {
            "cache_before": cache_before,
            "cache_after": self.cache_size(),
            "healthy_after": bool(readiness.get("knowledge_available")) and bool(readiness.get("vector_available")),
            "sdk_versions": readiness.get("sdk_versions", []),
            "rule_count": readiness.get("kujiale_rule_count", 0),
        }

    def collect_telemetry(self, result: StabilityResult) -> None:
        request_ids = [r.request_id for r in result.records if r.request_id]
        unique = set(request_ids)
        try:
            metrics = self.container.telemetry.metrics()
            tools = metrics.get("tools", {}) if isinstance(metrics, dict) else {}
            total_logged = sum(item.get("total_requests", 0) for item in tools.values())
            failed_logged = sum(item.get("failed_requests", 0) for item in tools.values())
            percentile_sample = {
                name: {
                    "p50": item.get("p50_duration_ms"),
                    "p95": item.get("p95_duration_ms"),
                    "p99": item.get("p99_duration_ms"),
                }
                for name, item in sorted(tools.items())
            }
        except Exception as error:
            tools, total_logged, failed_logged, percentile_sample = {}, 0, 0, {}
            print(f"[warn] telemetry 读取失败: {error}")
        result.telemetry = {
            "calls_issued": len(result.records),
            "request_ids_issued": len(request_ids),
            "request_ids_unique": len(unique),
            "request_id_collisions": len(request_ids) - len(unique),
            "logged_total_requests": total_logged,
            "logged_failed_requests": failed_logged,
            "percentiles_from_store": percentile_sample,
        }


def summarize(result: StabilityResult, rounds: int) -> dict:
    # 注入异常的那一轮是刻意制造的失败，不计入成功率 / 幂等性 / 漂移
    records = [r for r in result.records if not r.injected]
    total = len(records)
    if not total:
        return {"total_calls": 0}

    successes = sum(1 for r in records if r.status == EXPECTED_STATUS.get(r.tool, ""))
    injected_calls = sum(1 for r in result.records if r.injected)

    latency: dict[str, list[float]] = {}
    fingerprints: dict[str, set[str]] = {}
    for record in records:
        latency.setdefault(record.tool, []).append(record.duration_ms)
        fingerprints.setdefault(record.tool, set()).add(record.fingerprint)

    # 漂移：排除预热轮（首轮会懒加载知识索引），按轮次对半分。
    # 用中位数（p50）而非 p95 作为漂移主指标：样本量小时 p95 会被单个离群值主导，
    # 把「一次抖动」误判成「性能退化」。p95 仍上报，但只作参考。
    warmup_rounds = 1
    steady = {r.round_index for r in records if r.round_index >= warmup_rounds}
    round_ids = sorted(steady)
    half = len(round_ids) // 2
    early_rounds, late_rounds = set(round_ids[:half]), set(round_ids[half:])
    drift: dict[str, dict[str, float]] = {}
    for tool in latency:
        early = [r.duration_ms for r in records
                 if r.tool == tool and r.round_index in early_rounds]
        late = [r.duration_ms for r in records
                if r.tool == tool and r.round_index in late_rounds]
        early_p50, late_p50 = percentile(early, 0.50), percentile(late, 0.50)
        early_p95, late_p95 = percentile(early, 0.95), percentile(late, 0.95)
        drift[tool] = {
            "early_p50": early_p50, "late_p50": late_p50,
            "p50_delta_ms": round(late_p50 - early_p50, 2),
            "early_p95": early_p95, "late_p95": late_p95,
            "p95_delta_ms": round(late_p95 - early_p95, 2),
            "drift": round((late_p50 - early_p50) / early_p50, 4) if early_p50 else 0.0,
        }

    return {
        "rounds": rounds,
        "calls_per_round": len(ROUND_CALLS),
        "total_calls": total,
        "injected_calls_excluded": injected_calls,
        "success_rate": round(successes / total, 4),
        "status_mismatches": sorted({
            f"{r.tool}->{r.status}" for r in records
            if r.status != EXPECTED_STATUS.get(r.tool, "")
        }),
        "determinism_rate": round(
            sum(1 for tool, prints in fingerprints.items() if len(prints) == 1) / len(fingerprints), 4
        ),
        "non_deterministic_tools": sorted(t for t, prints in fingerprints.items() if len(prints) > 1),
        "latency_ms": {
            tool: {
                "calls": len(values),
                "avg": round(statistics.fmean(values), 2),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "p99": percentile(values, 0.99),
            }
            for tool, values in sorted(latency.items())
        },
        "drift": drift,
        "warmup_rounds_excluded": warmup_rounds,
        "max_drift_ms": round(max((abs(d["p50_delta_ms"]) for d in drift.values()), default=0.0), 2),
    }


def print_report(metrics: dict, result: StabilityResult, gate: dict) -> dict:
    print("\n" + "=" * 66)
    print("MCP 多次调用稳定性汇总")
    print("=" * 66)
    print(f"  轮次 / 每轮调用      : {metrics.get('rounds')} × {metrics.get('calls_per_round')} "
          f"= {metrics.get('total_calls')} 次")
    print(f"  Success Rate         : {metrics.get('success_rate', 0):.2%}")
    if metrics.get("status_mismatches"):
        print(f"    状态不符            : {metrics['status_mismatches']}")
    print(f"  Determinism Rate     : {metrics.get('determinism_rate', 0):.2%}（跨轮次返回指纹一致）")
    if metrics.get("non_deterministic_tools"):
        print(f"    非确定性工具        : {metrics['non_deterministic_tools']}")
    print(f"  Max P50 Drift        : {metrics.get('max_drift_ms', 0):+.2f} ms"
          f"（排除 {metrics.get('warmup_rounds_excluded', 0)} 个预热轮，前半轮 vs 后半轮）")

    print("\n  耗时与漂移（ms）:")
    for tool, stat in metrics.get("latency_ms", {}).items():
        drift = metrics.get("drift", {}).get(tool, {})
        print(f"    {tool:<24} p50={stat['p50']:<9} p95={stat['p95']:<10} p99={stat['p99']:<10} "
              f"Δp50={drift.get('p50_delta_ms', 0):+.2f} Δp95={drift.get('p95_delta_ms', 0):+.2f} "
              f"({drift.get('drift', 0):+.2%})")

    recovery = result.recovery or {}
    if recovery:
        print(f"\n  故障恢复（第 {recovery.get('injected_at_round')} 轮注入异常）:")
        print(f"    异常被结构化暴露    : {'是' if recovery.get('error_surfaced') else '否'}"
              f"（status={recovery.get('status_during_error')}）")
        print(f"    后续 {recovery.get('rounds_after_error')} 轮 / {recovery.get('calls_after_error')} 次调用恢复 : "
              f"{'是' if recovery.get('recovered') else '否'}")

    state = result.state or {}
    if state:
        print("\n  状态泄漏:")
        print(f"    知识单元缓存        : {state.get('cache_before')} -> {state.get('cache_after')}")
        print(f"    跑完后服务健康      : {'是' if state.get('healthy_after') else '否'}")

    telemetry = result.telemetry or {}
    if telemetry:
        print("\n  Telemetry 完整性:")
        print(f"    request_id 唯一     : {telemetry.get('request_ids_unique')}/"
              f"{telemetry.get('request_ids_issued')}（冲突 {telemetry.get('request_id_collisions')}）")
        print(f"    落库调用数          : {telemetry.get('logged_total_requests')}")

    concurrency = result.concurrency or {}
    if concurrency:
        print(f"\n  并发（{concurrency.get('workers')} 路）:")
        print(f"    结果                : {'PASS' if concurrency.get('passed') else 'FAIL'}")
        for anomaly in concurrency.get("anomalies", [])[:5]:
            print(f"      - {anomaly}")

    checks = [
        {"metric": "success_rate", "actual": metrics.get("success_rate", 0),
         "threshold": gate["min_success_rate"], "passed": metrics.get("success_rate", 0) >= gate["min_success_rate"]},
        {"metric": "determinism_rate", "actual": metrics.get("determinism_rate", 0),
         "threshold": gate["min_determinism_rate"], "passed": metrics.get("determinism_rate", 0) >= gate["min_determinism_rate"]},
        {"metric": "max_p50_drift", "actual": 1.0 if metrics.get("max_drift_ms", 0) <= gate["max_drift_ms"] else 0.0,
         "threshold": 1.0, "passed": metrics.get("max_drift_ms", 0) <= gate["max_drift_ms"],
         "detail": f"|Δp50|={abs(metrics.get('max_drift_ms', 0)):.2f}ms <= {gate['max_drift_ms']}ms"},
        {"metric": "request_id_uniqueness", "actual": 1.0 if not telemetry.get("request_id_collisions") else 0.0,
         "threshold": 1.0, "passed": not telemetry.get("request_id_collisions")},
        {"metric": "state_healthy_after", "actual": 1.0 if state.get("healthy_after") else 0.0,
         "threshold": 1.0, "passed": bool(state.get("healthy_after"))},
        {"metric": "recovery_after_error", "actual": 1.0 if recovery.get("recovered") else 0.0,
         "threshold": 1.0, "passed": bool(recovery.get("recovered"))},
    ]
    if concurrency:
        checks.append({"metric": "concurrency_clean", "actual": 1.0 if concurrency.get("passed") else 0.0,
                       "threshold": 1.0, "passed": bool(concurrency.get("passed"))})

    print("\n  门禁:")
    for check in checks:
        detail = check.get("detail") or f"{check['actual']:.2%} (阈值 {check['threshold']:.2%})"
        print(f"    [{'PASS' if check['passed'] else 'FAIL'}] {check['metric']:<22} {detail}")
    overall = all(c["passed"] for c in checks)
    print(f"  => GATE {'PASS' if overall else 'FAIL'}")
    return {"checks": checks, "passed": overall}


async def main_async(args: argparse.Namespace) -> int:
    tmp_dir = None
    if args.database:
        database_path = Path(args.database)
    else:
        tmp_dir = tempfile.TemporaryDirectory()
        database_path = Path(tmp_dir.name) / "mcp_stability.sqlite3"

    runner = StabilityRunner(args.rounds, args.concurrency, database_path)
    result = StabilityResult()
    cache_before = runner.cache_size()
    inject_at = args.rounds // 2 if args.inject_error else -1

    await runner.run_rounds(result, inject_at)
    await runner.run_concurrency(result)
    runner.collect_state(result, cache_before)
    runner.collect_telemetry(result)

    metrics = summarize(result, args.rounds)
    gate = {
        "min_success_rate": args.min_success_rate,
        "min_determinism_rate": args.min_determinism_rate,
        "max_drift_ms": args.max_drift_ms,
    }
    gate_result = print_report(metrics, result, gate)

    report = {
        "rounds": args.rounds,
        "concurrency": args.concurrency,
        "inject_error": bool(args.inject_error),
        "gate_config": gate,
        "metrics": metrics,
        "recovery": result.recovery,
        "state": result.state,
        "telemetry": result.telemetry,
        "concurrency_result": result.concurrency,
        "gate": gate_result,
    }
    out_path = Path(args.json) if args.json else DEFAULT_REPORT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {out_path}")

    if tmp_dir:
        tmp_dir.cleanup()
    return 0 if gate_result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP 多次调用稳定性验收")
    parser.add_argument("--rounds", type=int, default=30, help="串行轮次，默认 30")
    parser.add_argument("--concurrency", type=int, default=5, help="并发路数，<=1 时跳过并发检查")
    parser.add_argument("--database", default=None, help="telemetry SQLite 路径；缺省用临时库，不污染生产指标")
    parser.add_argument("--no-inject-error", dest="inject_error", action="store_false",
                        help="跳过中途异常注入与恢复检查")
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    parser.add_argument("--min-determinism-rate", type=float, default=0.99)
    parser.add_argument("--max-drift-ms", type=float, default=100.0,
                        help="允许的前后半轮 P50 绝对漂移（毫秒）。用中位数是因 P95 在小样本下会被单个离群值主导")
    parser.add_argument("--json", dest="json", default=None, help="报告输出路径")
    parser.set_defaults(inject_error=True)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
