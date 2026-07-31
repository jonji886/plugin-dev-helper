"""在不调用 LLM 的前提下执行检索质量门禁。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.run_eval import evaluate_retrieval, load_test_data
from vector_store import VectorStore


def main() -> int:
    metrics = evaluate_retrieval(VectorStore(), load_test_data())
    return 0 if metrics["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
