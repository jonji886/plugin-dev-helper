"""MCP 多轮调用序列评测（L2：跨步骤引用 / 指代传递）。

`benchmark/mcp_golden.json` 只验证单次调用是否正确；本脚本验证「上一步的返回能否
正确驱动下一步」——即 Coding Agent 实际使用时最常见的链式调用模式。

占位符 `{{steps.<序号>.<点号路径>}}` 引用前面步骤的返回字段。解析失败即该步骤失败，
这正是要测的能力：MCP 必须返回足够结构化、足够稳定的字段，Agent 才能接着用。

指标：
  Sequence Pass Rate          整条序列全部步骤通过的比例
  Step Pass Rate              单步骤通过比例
  Cross-Step Reference Rate   含占位符的步骤中成功解析并执行的比例
  Tool Chain Length           每条序列的工具链长度与耗时

用法：
  python scripts/run_mcp_sequences.py
  python scripts/run_mcp_sequences.py --sequence SEQ-02
  python scripts/run_mcp_sequences.py --json benchmark/results/mcp_sequences.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.run_mcp_eval import check_assertion, resolve  # noqa: E402

SEQUENCES_FILE = ROOT / "benchmark" / "mcp_sequences.json"
GATE_FILE = ROOT / "benchmark" / "mcp_gate.json"
DEFAULT_REPORT = ROOT / "benchmark" / "results" / "mcp_sequences_results.json"

PLACEHOLDER = re.compile(r"\{\{steps\.(\d+)\.([^}]+)\}\}")


def substitute(value: Any, payloads: list[dict]) -> tuple[Any, list[str]]:
    """递归替换占位符；返回 (替换后的值, 解析失败说明列表)。"""
    errors: list[str] = []
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            index, path = int(match.group(1)), match.group(2).strip()
            if index >= len(payloads):
                errors.append(f"引用了不存在的步骤 steps.{index}")
                return match.group(0)
            resolved = resolve(payloads[index], path)
            if resolved is None:
                errors.append(f"steps.{index}.{path} 解析为空")
                return match.group(0)
            if isinstance(resolved, (dict, list)):
                errors.append(f"steps.{index}.{path} 解析为集合，无法填入标量")
                return match.group(0)
            return str(resolved)

        return PLACEHOLDER.sub(repl, value), errors
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            replaced, sub_errors = substitute(item, payloads)
            result[key] = replaced
            errors.extend(sub_errors)
        return result, errors
    if isinstance(value, list):
        result = []
        for item in value:
            replaced, sub_errors = substitute(item, payloads)
            result.append(replaced)
            errors.extend(sub_errors)
        return result, errors
    return value, errors


class SequenceRunner:
    def __init__(self):
        from mcp_server.config import get_mcp_settings
        from mcp_server.runtime import build_container
        from mcp_server.server import create_mcp_server

        self.settings = get_mcp_settings()
        self.container = build_container(self.settings)
        try:
            self.container.retrieval.warmup()
        except Exception as error:
            print(f"[warn] embedding warmup skipped: {error}")
        self.mcp = create_mcp_server(self.settings, self.container)

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

    async def run_sequence(self, sequence: dict) -> dict:
        payloads: list[dict] = []
        step_records: list[dict] = []
        for index, step in enumerate(sequence.get("steps", [])):
            tool = step["tool"]
            arguments, errors = substitute(step.get("arguments", {}), payloads)
            has_placeholder = bool(PLACEHOLDER.search(json.dumps(step.get("arguments", {}), ensure_ascii=False)))

            if errors:
                step_records.append({
                    "index": index, "tool": tool, "passed": False,
                    "failures": errors, "arguments": arguments,
                    "uses_reference": has_placeholder, "reference_resolved": False,
                    "status": "", "duration_ms": None,
                })
                payloads.append({})
                continue

            outcome, payload = await self.call(tool, arguments)
            failures = (
                [f"入参被拒绝: {payload.get('__error__', '')[:150]}"]
                if outcome == "rejected"
                else check_assertion(f"{sequence['id']}#{index}", payload, step.get("expect", {}))
            )
            step_records.append({
                "index": index, "tool": tool, "passed": not failures, "failures": failures,
                "arguments": arguments, "uses_reference": has_placeholder,
                "reference_resolved": True if has_placeholder else None,
                "status": payload.get("status", ""), "duration_ms": payload.get("duration_ms"),
            })
            payloads.append(payload)

        total = len(step_records)
        passed = sum(1 for item in step_records if item["passed"])
        ref_steps = [item for item in step_records if item["uses_reference"]]
        ref_ok = sum(1 for item in ref_steps if item.get("reference_resolved") and item["passed"])
        return {
            "id": sequence["id"],
            "name": sequence.get("name", ""),
            "category": sequence.get("category", ""),
            "description": sequence.get("description", ""),
            "steps": step_records,
            "total_steps": total,
            "passed_steps": passed,
            "passed": passed == total,
            "reference_steps": len(ref_steps),
            "reference_ok": ref_ok,
            "chain": [item["tool"] for item in step_records],
            "total_ms": round(sum(item["duration_ms"] for item in step_records
                                  if isinstance(item.get("duration_ms"), (int, float))), 2),
        }


def summarize(results: list[dict]) -> dict:
    total_sequences = len(results)
    total_steps = sum(item["total_steps"] for item in results)
    passed_steps = sum(item["passed_steps"] for item in results)
    ref_steps = sum(item["reference_steps"] for item in results)
    ref_ok = sum(item["reference_ok"] for item in results)

    by_category: dict[str, list[bool]] = {}
    for item in results:
        by_category.setdefault(item["category"], []).append(item["passed"])

    return {
        "total_sequences": total_sequences,
        "passed_sequences": sum(1 for item in results if item["passed"]),
        "sequence_pass_rate": round(sum(1 for item in results if item["passed"]) / total_sequences, 4)
        if total_sequences else 0.0,
        "total_steps": total_steps,
        "passed_steps": passed_steps,
        "step_pass_rate": round(passed_steps / total_steps, 4) if total_steps else 0.0,
        "cross_step_reference_rate": round(ref_ok / ref_steps, 4) if ref_steps else 0.0,
        "reference_steps": ref_steps,
        "by_category": {
            name: {"total": len(values), "passed": sum(1 for v in values if v)}
            for name, values in sorted(by_category.items())
        },
    }


async def main_async(args: argparse.Namespace) -> int:
    data = json.loads(SEQUENCES_FILE.read_text(encoding="utf-8"))
    sequences = data["sequences"]
    if args.sequence:
        sequences = [s for s in sequences if s["id"] == args.sequence]
        if not sequences:
            print(f"未找到序列 {args.sequence}")
            return 2

    runner = SequenceRunner()
    results = []
    for sequence in sequences:
        record = await runner.run_sequence(sequence)
        results.append(record)
        flag = "PASS" if record["passed"] else "FAIL"
        print(f"[{flag}] {record['id']} {record['name']} "
              f"({record['passed_steps']}/{record['total_steps']} 步, "
              f"跨步引用 {record['reference_ok']}/{record['reference_steps']}, {record['total_ms']}ms)")
        print(f"       链: {' → '.join(record['chain'])}")
        for step in record["steps"]:
            if not step["passed"]:
                print(f"       - step{step['index']} {step['tool']}: {'; '.join(step['failures'])}")

    metrics = summarize(results)
    print("\n" + "=" * 64)
    print("MCP 多轮调用序列评测汇总")
    print("=" * 64)
    print(f"  Sequence Pass Rate       : {metrics['passed_sequences']}/{metrics['total_sequences']} = "
          f"{metrics['sequence_pass_rate']:.2%}")
    print(f"  Step Pass Rate           : {metrics['passed_steps']}/{metrics['total_steps']} = "
          f"{metrics['step_pass_rate']:.2%}")
    print(f"  Cross-Step Reference Rate: {metrics['cross_step_reference_rate']:.2%}"
          f"（{metrics['reference_steps']} 个含占位符引用的步骤）")

    gate_result = None
    if GATE_FILE.exists():
        gate = json.loads(GATE_FILE.read_text(encoding="utf-8"))
        checks = []
        for key in ("sequence_pass_rate", "cross_step_reference_rate"):
            if key in gate:
                actual = metrics[key]
                checks.append({"metric": key, "actual": actual, "threshold": gate[key],
                               "passed": actual >= gate[key]})
        if checks:
            print("\n  门禁:")
            for check in checks:
                print(f"    [{'PASS' if check['passed'] else 'FAIL'}] {check['metric']:<28} "
                      f"{check['actual']:.2%} >= {check['threshold']:.2%}")
            gate_result = {"checks": checks, "passed": all(c["passed"] for c in checks)}
            print(f"  => GATE {'PASS' if gate_result['passed'] else 'FAIL'}")

    out_path = Path(args.json) if args.json else DEFAULT_REPORT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"metrics": metrics, "gate": gate_result, "sequences": results},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {out_path}")

    failed = metrics["total_sequences"] - metrics["passed_sequences"]
    if gate_result and not gate_result["passed"]:
        return 1
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP 多轮调用序列评测")
    parser.add_argument("--sequence", default=None, help="只跑指定序列 id")
    parser.add_argument("--json", dest="json", default=None, help="报告输出路径")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
