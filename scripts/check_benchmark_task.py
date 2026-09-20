"""评测任务验收脚本。

对 benchmark/tasks.json 中的任务执行自动化验收：
  1. typecheck：用 tsc 对 fixture 工程做类型检查（--noEmit）
  2. build：   用 tsc 产出 dist（真实构建步骤）
  3. tests：   "contains:" / "not_contains:" 字符串断言

两种模式：
  --mode initial   验收 fixture 初始态（未解题）。用于确认任务「非真空」：
                   除声明 initial_expect_fail=false 的任务外，初始态应当失败。
  --mode solution  把 benchmark/solutions/<id>/vm.ts 参考解写进 fixture 后验收，
                   验收完还原。用于证明任务可解、acceptance 可被满足。
  --mode both      依次跑 initial + solution（默认）。

用法：
  python scripts/check_benchmark_task.py T01                 # 验收单个任务（both）
  python scripts/check_benchmark_task.py --all --mode initial
  python scripts/check_benchmark_task.py --all --mode solution
  python scripts/check_benchmark_task.py --all --json benchmark/results/acceptance.json
  python scripts/check_benchmark_task.py --clean             # 清理所有 dist 产物
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "benchmark" / "tasks.json"
FIXTURES = ROOT / "benchmark" / "fixtures"
SOLUTIONS = ROOT / "benchmark" / "solutions"
SDK_DTS = ROOT / "node_modules" / "@manycore" / "idp-sdk" / "index.d.ts"

MODES = ("initial", "solution", "both")


def _find_tsc() -> list[str]:
    candidates = [
        ROOT / "frontend" / "node_modules" / ".bin" / "tsc",
        ROOT / "node_modules" / ".bin" / "tsc",
    ]
    for c in candidates:
        if c.exists():
            return [str(c)]
    # 兜底：依赖本机 tsc
    return ["tsc"]


def _run(cmd: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    ok = proc.returncode == 0
    output = (proc.stdout or "") + (proc.stderr or "")
    return ok, output.strip()


def _assertion(passed: bool, name: str) -> tuple[bool, str]:
    return passed, f"  [{'PASS' if passed else 'FAIL'}] {name}"


def _source_text(src_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(src_dir.glob("*.ts")))


def _run_acceptance(task: dict, fixture: Path, src_dir: Path, tsc: list[str]) -> tuple[bool, list[str]]:
    """执行 acceptance（typecheck / build / 字符串断言），返回 (是否通过, 结果行)。"""
    acceptance = task.get("acceptance", {})
    src_text = _source_text(src_dir)
    results: list[tuple[bool, str]] = []

    # 1) typecheck —— 受 acceptance.typecheck 开关控制（缺失时默认开启）
    if acceptance.get("typecheck", True):
        ok, out = _run(tsc + ["--noEmit", "-p", str(fixture / "tsconfig.json")])
        results.append(_assertion(ok, "typecheck"))
        if not ok:
            for line in out.splitlines()[:6]:
                print(f"      {line}")

    # 2) build —— 受 acceptance.build 开关控制（缺失时默认开启）
    if acceptance.get("build", True):
        ok, out = _run(tsc + ["-p", str(fixture / "tsconfig.json"), "--noEmit", "false",
                              "--outDir", str(fixture / "dist")])
        results.append(_assertion(ok, "build"))

    # 3) 字符串断言
    for test in acceptance.get("tests", []):
        if test.startswith("contains:"):
            needle = test[len("contains:"):]
            results.append(_assertion(needle in src_text, f"contains:{needle}"))
        elif test.startswith("not_contains:"):
            needle = test[len("not_contains:"):]
            results.append(_assertion(needle not in src_text, f"not_contains:{needle}"))

    lines = [text for _, text in results]
    return all(ok for ok, _ in results), lines


def check_task(task: dict, mode: str = "both", clean: bool = False) -> dict:
    """验收单个任务，返回结构化结果。"""
    task_id = task["id"]
    fixture = FIXTURES / task["initial_project"].split("/", 1)[-1]
    src_dir = fixture / "src"
    target = src_dir / "vm.ts"
    tsc = _find_tsc()

    outcome: dict = {
        "id": task_id,
        "title": task.get("title", ""),
        "category": task.get("category", ""),
        "initial": None,
        "solution": None,
        "verdict": "FAIL",
    }

    print(f"\n=== {task_id} {task['title']} ===")

    if clean:
        dist_dir = fixture / "dist"
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
            print("  [clean] removed dist")
        outcome["verdict"] = "CLEAN"
        return outcome

    if not src_dir.exists() or not (fixture / "tsconfig.json").exists():
        print(f"  [FAIL] fixture 缺失: {fixture}")
        return outcome
    if not SDK_DTS.exists():
        print(f"  [FAIL] SDK 类型缺失: {SDK_DTS}")
        return outcome

    initial_expect_fail = bool(task.get("acceptance", {}).get("initial_expect_fail", True))

    # ---- initial 模式 ----
    if mode in ("initial", "both"):
        print("  -- initial（fixture 初始态）--")
        passed, lines = _run_acceptance(task, fixture, src_dir, tsc)
        for line in lines:
            print(line)
        if initial_expect_fail:
            # 初始态应当失败：通过说明该任务对 Agent 没有区分度（acceptance 真空）
            non_vacuous = not passed
            print(f"  => initial {'FAIL（符合预期）' if non_vacuous else 'PASS（验收真空，任务无区分度）'}")
            outcome["initial"] = {"passed": passed, "non_vacuous": non_vacuous}
        else:
            print(f"  => initial {'PASS' if passed else 'FAIL'}（该任务初始态即通过验收，需依赖轨迹判定）")
            outcome["initial"] = {"passed": passed, "non_vacuous": None}

    # ---- solution 模式 ----
    if mode in ("solution", "both"):
        solution_rel = task.get("reference_solution")
        solution_path = ROOT / "benchmark" / solution_rel if solution_rel else SOLUTIONS / task_id / "vm.ts"
        print("  -- solution（参考解）--")
        if not solution_path.exists():
            print(f"  [FAIL] 参考解缺失: {solution_path}")
            outcome["solution"] = {"passed": False, "missing": True}
            return outcome

        original = target.read_text(encoding="utf-8")
        try:
            target.write_text(solution_path.read_text(encoding="utf-8"), encoding="utf-8")
            passed, lines = _run_acceptance(task, fixture, src_dir, tsc)
        finally:
            target.write_text(original, encoding="utf-8")
        for line in lines:
            print(line)
        print(f"  => solution {'PASS' if passed else 'FAIL'}")
        outcome["solution"] = {"passed": passed, "missing": False}

    # ---- 综合判定 ----
    if mode == "initial":
        outcome["verdict"] = "PASS" if outcome["initial"] and outcome["initial"]["non_vacuous"] is not False else "FAIL"
    elif mode == "solution":
        outcome["verdict"] = "PASS" if outcome["solution"] and outcome["solution"]["passed"] else "FAIL"
    else:
        initial_ok = outcome["initial"] and outcome["initial"].get("non_vacuous") is not False
        solution_ok = outcome["solution"] and outcome["solution"]["passed"]
        outcome["verdict"] = "PASS" if (initial_ok and solution_ok) else "FAIL"
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id", nargs="?", default=None, help="任务 id，如 T01")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--mode", choices=MODES, default="both", help="initial / solution / both")
    parser.add_argument("--json", dest="json_path", default=None, help="把结果写入 JSON 文件")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))["tasks"]

    if args.task_id:
        tasks = [t for t in tasks if t["id"] == args.task_id]
        if not tasks:
            print(f"未找到任务 {args.task_id}")
            return 2

    results = [check_task(t, mode=args.mode, clean=args.clean) for t in tasks]

    if args.clean:
        return 0

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"\n结果: {passed}/{len(results)} 通过（mode={args.mode}）")

    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"mode": args.mode, "results": results}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"结果已写入: {out}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
