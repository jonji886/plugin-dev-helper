"""MCP Tool 遥测指标测试。

`mcp_metrics()` 是稳定性验收（scripts/check_mcp_stability.py）的数据来源，
此前只有 avg_duration_ms，无法判断延迟漂移；这里覆盖新增的分位数字段。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.metrics_store import MetricsStore


class McpMetricsTests(unittest.TestCase):
    def _store(self) -> MetricsStore:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return MetricsStore(Path(self._tmp.name) / "app.sqlite3")

    def test_mcp_metrics_exposes_latency_percentiles_per_tool(self):
        store = self._store()
        # 10 次调用：1ms..10ms，p50 应落在 5~6ms 区间，p95/p99 接近上限
        for index in range(1, 11):
            store.record_mcp_tool({
                "request_id": f"mcp-{index}",
                "tool_name": "get_api",
                "status": "error" if index == 10 else "success",
                "duration_ms": float(index),
                "result_count": 1,
                "query": "IDP.Miniapp.exit",
            })

        metrics = store.mcp_metrics()
        tool = metrics["tools"]["get_api"]

        self.assertEqual(tool["total_requests"], 10)
        self.assertEqual(tool["failed_requests"], 1)
        self.assertEqual(tool["failure_rate"], 0.1)
        for key in ("p50_duration_ms", "p95_duration_ms", "p99_duration_ms"):
            self.assertIn(key, tool)
        self.assertGreaterEqual(tool["p50_duration_ms"], tool["min_duration_ms"])
        self.assertLessEqual(tool["p50_duration_ms"], tool["max_duration_ms"])
        self.assertGreaterEqual(tool["p95_duration_ms"], tool["p50_duration_ms"])
        self.assertGreaterEqual(tool["p99_duration_ms"], tool["p95_duration_ms"])

    def test_mcp_metrics_handles_single_sample_without_crashing(self):
        store = self._store()
        store.record_mcp_tool({
            "request_id": "mcp-single",
            "tool_name": "get_type",
            "status": "success",
            "duration_ms": 3.5,
            "result_count": 2,
            "query": "MiniappUploadDataOption",
        })

        tool = store.mcp_metrics()["tools"]["get_type"]
        # 单样本时分位数退化为该样本本身，不应抛异常也不应返回 0
        self.assertEqual(tool["p50_duration_ms"], 3.5)
        self.assertEqual(tool["p95_duration_ms"], 3.5)
        self.assertEqual(tool["p99_duration_ms"], 3.5)
        self.assertEqual(tool["result_count"], 2)

    def test_mcp_metrics_returns_empty_tools_without_records(self):
        store = self._store()
        self.assertEqual(store.mcp_metrics()["tools"], {})


if __name__ == "__main__":
    unittest.main()
