import tempfile
import unittest
from pathlib import Path

from app.metrics_store import MetricsStore


class MetricsStoreTests(unittest.TestCase):
    def test_metrics_and_feedback_use_a_consistent_recent_request_window(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MetricsStore(Path(directory) / "app.sqlite3")
            for index in range(1001):
                store.record_request({
                    "request_id": f"request-{index}",
                    "session_id": "a1b2c3d4",
                    "query": f"question-{index}",
                    "status": "error" if index == 0 else "success",
                    "retrieval_ms": float(index),
                    "llm_ms": float(index),
                    "total_ms": float(index),
                })

            # 窗口外的反馈不应影响最近 1,000 条请求的反馈率。
            store.record_feedback("request-0", True, "old")
            store.record_feedback("request-1000", True, "first")
            store.record_feedback("request-1000", False, "latest")
            metrics = store.metrics()

        self.assertEqual(metrics["window_limit"], 1000)
        self.assertEqual(metrics["total_requests"], 1000)
        self.assertEqual(metrics["success_rate"], 1.0)
        self.assertEqual(metrics["feedback_total"], 1)
        self.assertEqual(metrics["helpful_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
