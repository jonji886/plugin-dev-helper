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

    def test_failure_cases_collect_errors_missing_retrieval_and_negative_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MetricsStore(Path(directory) / "app.sqlite3")
            store.record_request({
                "request_id": "ok",
                "session_id": "a1b2c3d4",
                "query": "正常问题",
                "status": "success",
                "retrieved_count": 1,
                "citation_count": 1,
            })
            store.record_request({
                "request_id": "negative",
                "session_id": "a1b2c3d4",
                "query": "用户认为不准确的问题",
                "status": "success",
                "retrieved_count": 1,
                "citation_count": 1,
            })
            store.record_feedback("negative", False, "接口名称不对")
            store.record_request({
                "request_id": "error",
                "session_id": "a1b2c3d4",
                "query": "服务失败的问题",
                "status": "error",
                "retrieved_count": 0,
                "citation_count": 0,
                "error_message": "模型超时",
            })

            cases = store.failure_cases()

        case_by_id = {case["request_id"]: case for case in cases}
        self.assertNotIn("ok", case_by_id)
        self.assertEqual(case_by_id["negative"]["failure_reasons"], ["negative_feedback"])
        self.assertEqual(case_by_id["negative"]["feedback_comment"], "接口名称不对")
        self.assertEqual(case_by_id["error"]["failure_reasons"], [
            "request_error", "no_retrieval"
        ])


if __name__ == "__main__":
    unittest.main()
