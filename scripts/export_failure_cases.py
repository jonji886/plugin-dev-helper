"""导出需要人工复核的请求，生成可标注的回归案例候选 JSONL。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.metrics_store import MetricsStore


def build_candidate(case: dict) -> dict:
    """将运行记录转换成评测案例草稿，期望答案和来源由人工补齐。"""
    return {
        "id": f"feedback-{case['request_id']}",
        "category": "regression",
        "question": case["query"],
        "expected_answer": "",
        "reference_docs": [],
        "source_request_id": case["request_id"],
        "failure_reasons": case["failure_reasons"],
        "feedback_comment": case["feedback_comment"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("APP_DATABASE_PATH", "data/app.sqlite3")),
        help="SQLite 指标库路径，默认读取 APP_DATABASE_PATH 或 data/app.sqlite3",
    )
    parser.add_argument("--output", type=Path, required=True, help="候选 JSONL 输出路径")
    parser.add_argument("--limit", type=int, default=100, help="最多导出多少条候选")
    args = parser.parse_args()

    cases = MetricsStore(args.database).failure_cases(args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for case in cases:
            output.write(json.dumps(build_candidate(case), ensure_ascii=False) + "\n")

    print(f"已导出 {len(cases)} 条失败案例候选: {args.output}")
    print("请人工补齐 expected_answer、expected_keywords 和 reference_docs，"
          "再合并到 eval/regression_cases.json。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
