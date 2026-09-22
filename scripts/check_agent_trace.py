"""Agent 调用轨迹评分器（L2：工具选择与调用序列）。

benchmark/tasks.json 里的 `mcp_tools_expected` 与 `reference_symbols` 此前是死字段，
没有任何脚本消费。本脚本把真实 Agent 跑任务时落盘的 MCP 调用轨迹拿来评分。

轨迹格式见 benchmark/traces/README.md。

指标：
  Tool Selection Precision / Recall / F1   相对 mcp_tools_expected
  Symbol Coverage                          相对 reference_symbols
  Redundant Call Rate                      同 (tool, 归一化入参) 的重复调用占比
  Sequence Compliance                      constraints 先于 scaffold；validate 先查证后自检
  Abstention Correctness                   reject_hallucination 类任务必须明确说明而非编造
  Task Acceptance                          由 check_benchmark_task.py 产出，可经 --acceptance 关联

用法：
  python scripts/check_agent_trace.py benchmark/traces/<run>.json
  python scripts/check_agent_trace.py --dir benchmark/traces
  python scripts/check_agent_trace.py --self-test      # 用内置合成轨迹验证评分器自身
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "benchmark" / "tasks.json"
TRACES_DIR = ROOT / "benchmark" / "traces"
DEFAULT_REPORT = ROOT / "benchmark" / "results" / "agent_trace_results.json"

# 序列规则：(后置工具, 必须先行出现的工具集合, 规则说明)
SEQUENCE_RULES: list[tuple[str, set[str], str]] = [
    ("get_plugin_scaffold", {"get_plugin_constraints"}, "开发插件前必须先取约束"),
    ("validate_api_usage", {"get_api", "get_type"}, "自检前必须先查证 API/类型定义"),
]


def load_tasks() -> dict[str, dict]:
    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))["tasks"]
    return {task["id"]: task for task in tasks}


def call_key(call: dict) -> tuple[str, str]:
    """归一化一次调用，用于识别冗余调用。"""
    arguments = call.get("arguments", {}) or {}
    normalized = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return call.get("tool", ""), normalized


def score_task(task: dict, trace_task: dict) -> dict:
    tool_exp = task.get("tool_expectation") or {}
    # 向后兼容：未声明 tool_expectation 时从 mcp_tools_expected 降级推导
    required_tools = set(tool_exp.get("required_tools") or task.get("mcp_tools_expected", []))
    optional_tools = set(tool_exp.get("optional_tools", []))
    forbidden_tools = set(tool_exp.get("forbidden_tools", []))
    acceptable_sets = tool_exp.get("acceptable_tool_sets", []) or []
    reference_symbols = set(task.get("reference_symbols", []))
    calls: list[dict] = trace_task.get("calls", []) or []
    used_tools = {call.get("tool", "") for call in calls if call.get("tool")}
    expected_for_precision = required_tools | optional_tools

    # ---- 工具选择 ----
    # 核心原则：Task Outcome > Prescribed Tool Path。precision 仅惩罚「超出 required∪optional」
    # 的调用；recall 仅考核 required 工具是否被覆盖。高效 Agent 用更少工具完成不应被惩罚。
    hits = expected_for_precision & used_tools
    precision = len(hits) / len(used_tools) if used_tools else 0.0
    recall = len(required_tools & used_tools) / len(required_tools) if required_tools else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    missing_tools = sorted(required_tools - used_tools)
    extra_tools = sorted(used_tools - expected_for_precision)

    # ---- 工具路径合法性（独立于 Task 成败）----
    required_satisfied = required_tools.issubset(used_tools) if required_tools else True
    if acceptable_sets:
        acceptable_satisfied = required_satisfied or any(
            set(s).issubset(used_tools) for s in acceptable_sets if s
        )
    else:
        acceptable_satisfied = required_satisfied
    forbidden_violation = bool(forbidden_tools & used_tools)
    redundant_tools = sorted(used_tools - expected_for_precision)

    # ---- 符号覆盖 ----
    queried = set(trace_task.get("symbols_queried", []) or [])
    for call in calls:
        arguments = call.get("arguments", {}) or {}
        for key in ("symbol", "name"):
            if arguments.get(key):
                queried.add(arguments[key])
    matched_symbols = sorted(reference_symbols & queried)
    missing_symbols = sorted(reference_symbols - queried)
    symbol_coverage = len(matched_symbols) / len(reference_symbols) if reference_symbols else 0.0

    # ---- 冗余调用 ----
    seen: set[tuple[str, str]] = set()
    redundant = 0
    for call in calls:
        key = call_key(call)
        if key in seen:
            redundant += 1
        else:
            seen.add(key)
    redundant_rate = redundant / len(calls) if calls else 0.0

    # ---- 序列合规 ----
    sequence_results = []
    for index, call in enumerate(calls):
        tool = call.get("tool", "")
        for later, prerequisites, reason in SEQUENCE_RULES:
            if tool != later:
                continue
            prior = {c.get("tool", "") for c in calls[:index]}
            ok = bool(prior & prerequisites)
            sequence_results.append({
                "rule": f"{' / '.join(sorted(prerequisites))} 先于 {later}",
                "reason": reason,
                "passed": ok,
                "position": index + 1,
            })
    sequence_rate = (
        sum(1 for item in sequence_results if item["passed"]) / len(sequence_results)
        if sequence_results else 1.0
    )

    # ---- 拒答正确性 ----
    needs_abstention = task.get("category") == "reject_hallucination"
    abstained = bool(trace_task.get("abstained", False))
    abstention_ok = (not needs_abstention) or abstained

    return {
        "task_id": task["id"],
        "category": task.get("category", ""),
        "accepted": bool(trace_task.get("accepted", False)),
        "call_count": len(calls),
        "tool_selection": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "missing_tools": missing_tools,
            "extra_tools": extra_tools,
        },
        "tool_expectation": {
            "required_tools": sorted(required_tools),
            "optional_tools": sorted(optional_tools),
            "forbidden_tools": sorted(forbidden_tools),
            "required_satisfied": bool(required_satisfied),
            "acceptable_satisfied": bool(acceptable_satisfied),
            "forbidden_violation": bool(forbidden_violation),
            "redundant_tools": redundant_tools,
        },
        "symbol_coverage": round(symbol_coverage, 4),
        "missing_symbols": missing_symbols,
        "redundant_call_rate": round(redundant_rate, 4),
        "redundant_calls": redundant,
        "sequence": {
            "rate": round(sequence_rate, 4),
            "checks": sequence_results,
        },
        "abstention": {
            "required": needs_abstention,
            "abstained": abstained,
            "passed": abstention_ok,
        },
    }


def summarize(per_task: list[dict]) -> dict:
    total = len(per_task)
    if not total:
        return {"total_tasks": 0}

    def avg(key: str) -> float:
        return round(sum(item[key] for item in per_task) / total, 4)

    return {
        "total_tasks": total,
        "accepted_tasks": sum(1 for item in per_task if item["accepted"]),
        "tool_selection_precision": avg_precision(per_task),
        "tool_selection_recall": avg_recall(per_task),
        "tool_selection_f1": avg_f1(per_task),
        "symbol_coverage": avg("symbol_coverage"),
        "redundant_call_rate": avg("redundant_call_rate"),
        "sequence_compliance": avg_sequence(per_task),
        "abstention_correctness": round(
            sum(1 for item in per_task if item["abstention"]["passed"]) / total, 4
        ),
        "avg_calls_per_task": round(sum(item["call_count"] for item in per_task) / total, 2),
    }


def avg_precision(per_task: list[dict]) -> float:
    return round(sum(i["tool_selection"]["precision"] for i in per_task) / len(per_task), 4)


def avg_recall(per_task: list[dict]) -> float:
    return round(sum(i["tool_selection"]["recall"] for i in per_task) / len(per_task), 4)


def avg_f1(per_task: list[dict]) -> float:
    return round(sum(i["tool_selection"]["f1"] for i in per_task) / len(per_task), 4)


def avg_sequence(per_task: list[dict]) -> float:
    return round(sum(i["sequence"]["rate"] for i in per_task) / len(per_task), 4)


def score_run(payload: dict) -> dict:
    tasks = load_tasks()
    per_task = []
    unknown = []
    for trace_task in payload.get("tasks", []):
        task = tasks.get(trace_task.get("task_id", ""))
        if task is None:
            unknown.append(trace_task.get("task_id", ""))
            continue
        per_task.append(score_task(task, trace_task))
    return {
        "run_id": payload.get("run_id", ""),
        "agent": payload.get("agent", ""),
        "condition": payload.get("condition", ""),
        "per_task": per_task,
        "unknown_task_ids": unknown,
        "summary": summarize(per_task),
    }


def print_report(result: dict) -> None:
    print(f"\n=== run={result['run_id'] or '-'} agent={result['agent'] or '-'} "
          f"condition={result['condition'] or '-'} ===")
    for item in result["per_task"]:
        selection = item["tool_selection"]
        flags = []
        if not item["accepted"]:
            flags.append("未通过验收")
        if selection["missing_tools"]:
            flags.append(f"漏调 {selection['missing_tools']}")
        if selection["extra_tools"]:
            flags.append(f"多调 {selection['extra_tools']}")
        if item["missing_symbols"]:
            flags.append(f"未查符号 {item['missing_symbols']}")
        if not item["abstention"]["passed"]:
            flags.append("应明确说明不存在却未说明")
        note = "；".join(flags) if flags else "OK"
        print(f"  {item['task_id']} calls={item['call_count']:<3} "
              f"F1={selection['f1']:.2f} 符号覆盖={item['symbol_coverage']:.0%} "
              f"冗余={item['redundant_call_rate']:.0%} 序列={item['sequence']['rate']:.0%} | {note}")
    summary = result["summary"]
    if summary.get("total_tasks"):
        print(f"  -- 汇总（{summary['total_tasks']} 个任务）--")
        print(f"    验收通过        : {summary['accepted_tasks']}/{summary['total_tasks']}")
        print(f"    Tool Selection  : P={summary['tool_selection_precision']:.2%} "
              f"R={summary['tool_selection_recall']:.2%} F1={summary['tool_selection_f1']:.2%}")
        print(f"    Symbol Coverage : {summary['symbol_coverage']:.2%}")
        print(f"    Redundant Rate  : {summary['redundant_call_rate']:.2%}")
        print(f"    Sequence Comp.  : {summary['sequence_compliance']:.2%}")
        print(f"    Abstention      : {summary['abstention_correctness']:.2%}")
        print(f"    平均调用次数    : {summary['avg_calls_per_task']}")


SYNTHETIC_TRACE: dict[str, Any] = {
    "run_id": "self-test-synthetic",
    "agent": "synthetic（仅用于验证评分器本身，不是真实 Agent 结果）",
    "condition": "with_mcp",
    "tasks": [
        {
            "task_id": "T01",
            "accepted": True,
            "calls": [
                {"seq": 1, "tool": "get_api", "arguments": {"symbol": "IDP.Miniapp.exit"}, "status": "ok"},
                {"seq": 2, "tool": "search_docs", "arguments": {"query": "退出小程序"}, "status": "ok"},
            ],
            "symbols_queried": ["IDP.Miniapp.exit"],
        },
        {
            "task_id": "T09",
            "accepted": False,
            "abstained": True,
            "calls": [
                {"seq": 1, "tool": "get_api", "arguments": {"symbol": "IDP.Miniapp.closeMiniapp"}, "status": "not_found"},
                {"seq": 2, "tool": "search_docs", "arguments": {"query": "关闭小程序"}, "status": "ok"},
                {"seq": 3, "tool": "search_docs", "arguments": {"query": "关闭小程序"}, "status": "ok"},
            ],
            "symbols_queried": ["IDP.Miniapp.closeMiniapp"],
        },
    ],
}


def self_test() -> int:
    """用合成轨迹验证评分器自身。明确标注：这不是任何 Agent 的实测结果。"""
    print("[self-test] 使用内置合成轨迹，仅验证评分器逻辑，不代表任何 Agent 实测结果。")
    result = score_run(SYNTHETIC_TRACE)
    print_report(result)
    first, ninth = result["per_task"][0], result["per_task"][1]
    checks = [
        ("T01 工具选择 F1 满分", first["tool_selection"]["f1"] == 1.0),
        ("T01 符号覆盖 100%", first["symbol_coverage"] == 1.0),
        ("T09 冗余调用被识别", ninth["redundant_calls"] == 1),
        ("T09 拒答正确", ninth["abstention"]["passed"]),
        ("T09 未查到期望符号被记录", bool(ninth["missing_symbols"])),
    ]
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in checks) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 调用轨迹评分器")
    parser.add_argument("trace", nargs="?", default=None, help="轨迹 JSON 文件")
    parser.add_argument("--dir", default=None, help="批量评分目录下的所有 *.json 轨迹")
    parser.add_argument("--self-test", action="store_true", help="用内置合成轨迹验证评分器")
    parser.add_argument("--json", dest="json_path", default=None, help="报告输出路径")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    paths: list[Path] = []
    if args.dir:
        paths = sorted(p for p in Path(args.dir).glob("*.json") if p.name != "README.md")
    elif args.trace:
        paths = [Path(args.trace)]
    else:
        parser.error("需要指定轨迹文件、--dir 或 --self-test")

    if not paths:
        print(f"[info] 目录中没有轨迹文件：{args.dir}。真实 Agent 跑完任务后按 "
              f"benchmark/traces/README.md 落盘轨迹即可评分。")
        return 0

    results = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = score_run(payload)
        result["source_file"] = path.name
        results.append(result)
        print_report(result)

    out_path = Path(args.json_path) if args.json_path else DEFAULT_REPORT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"runs": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
