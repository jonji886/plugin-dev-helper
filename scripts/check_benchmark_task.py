"""评测任务验收脚本。

对 benchmark/tasks.json 中的任务执行自动化验收：
  1. typecheck：用 tsc 对 fixture 工程做类型检查（--noEmit）
  2. build：   用 tsc 产出 dist（真实构建步骤）
  3. tests：   "contains:" / "not_contains:" 字符串断言

用法：
  python scripts/check_benchmark_task.py T01            # 验收单个任务
  python scripts/check_benchmark_task.py --all          # 验收全部任务
  python scripts/check_benchmark_task.py --clean        # 清理所有 dist 产物
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
SDK_DTS = ROOT / "node_modules" / "@manycore" / "idp-sdk" / "index.d.ts"


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


def check_task(task: dict, clean: bool = False) -> bool:
    task_id = task["id"]
    fixture = FIXTURES / task["initial_project"].split("/", 1)[-1]
    src_dir = fixture / "src"
    tsconfig = fixture / "tsconfig.json"
    dist_dir = fixture / "dist"
    tsc = _find_tsc()

    print(f"\n=== {task_id} {task['title']} ===")

    if clean:
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
            print("  [clean] removed dist")
        return True

    if not src_dir.exists() or not tsconfig.exists():
        print(f"  [FAIL] fixture 缺失: {fixture}")
        return False
    if not SDK_DTS.exists():
        print(f"  [FAIL] SDK 类型缺失: {SDK_DTS}")
        return False

    results: list[tuple[bool, str]] = []

    # 1) typecheck
    ok, out = _run(tsc + ["--noEmit", "-p", str(tsconfig)])
    results.append(_assertion(ok, "typecheck"))
    if not ok:
        print("  typecheck errors:")
        for line in out.splitlines()[:8]:
            print("    " + line)

    # 2) build
    ok, out = _run(tsc + ["-p", str(tsconfig), "--noEmit", "false", "--outDir", str(dist_dir)])
    results.append(_assertion(ok, "build"))

    # 3) assertions
    acc = task.get("acceptance", {})
    src_text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(src_dir.glob("*.ts")))
    for test in acc.get("tests", []):
        if test.startswith("contains:"):
            needle = test[len("contains:"):]
            results.append(_assertion(needle in src_text, f"contains:{needle}"))
        elif test.startswith("not_contains:"):
            needle = test[len("not_contains:"):]
            results.append(_assertion(needle not in src_text, f"not_contains:{needle}"))

    all_pass = all(ok for ok, _ in results)
    print(f"  => {'PASS' if all_pass else 'FAIL'}")
    return all_pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id", nargs="?", default=None, help="任务 id，如 T01")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))["tasks"]

    if args.task_id:
        tasks = [t for t in tasks if t["id"] == args.task_id]
        if not tasks:
            print(f"未找到任务 {args.task_id}")
            return 2

    passed = 0
    for t in tasks:
        if check_task(t, clean=args.clean):
            passed += 1

    if not args.clean:
        print(f"\n结果: {passed}/{len(tasks)} 通过")
        return 0 if passed == len(tasks) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
