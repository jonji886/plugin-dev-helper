"""SQLite 请求指标与用户反馈存储。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from statistics import quantiles


class MetricsStore:
    METRICS_WINDOW_SIZE = 1000

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS request_logs (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    retrieved_count INTEGER NOT NULL,
                    citation_count INTEGER NOT NULL,
                    retrieval_ms REAL NOT NULL,
                    llm_ms REAL NOT NULL,
                    total_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    helpful INTEGER NOT NULL,
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (request_id) REFERENCES request_logs(request_id)
                );
            """)
            # 旧版本允许重复反馈；保留每个请求最近一次选择后再加唯一约束。
            connection.execute("""
                DELETE FROM feedback
                WHERE id NOT IN (SELECT MAX(id) FROM feedback GROUP BY request_id)
            """)
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_request_id ON feedback(request_id)"
            )

    def record_request(self, record: dict) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO request_logs (
                    request_id, session_id, query, intent, retrieved_count, citation_count,
                    retrieval_ms, llm_ms, total_ms, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["request_id"], record["session_id"], record["query"],
                record.get("intent", ""), record.get("retrieved_count", 0),
                record.get("citation_count", 0), record.get("retrieval_ms", 0.0),
                record.get("llm_ms", 0.0), record.get("total_ms", 0.0),
                record["status"], record.get("error_message"),
            ))

    def record_feedback(self, request_id: str, helpful: bool, comment: str) -> bool:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM request_logs WHERE request_id = ?", (request_id,)
            ).fetchone()
            if not exists:
                return False
            connection.execute("""
                INSERT INTO feedback (request_id, helpful, comment) VALUES (?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    helpful = excluded.helpful,
                    comment = excluded.comment,
                    created_at = CURRENT_TIMESTAMP
            """, (request_id, int(helpful), comment))
        return True

    def metrics(self) -> dict:
        with self._connect() as connection:
            summary = connection.execute(f"""
                WITH recent_requests AS (
                    SELECT rowid, * FROM request_logs
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT {self.METRICS_WINDOW_SIZE}
                )
                SELECT
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(status = 'success'), 0) AS successful_requests,
                    COALESCE(AVG(retrieval_ms), 0) AS avg_retrieval_ms,
                    COALESCE(AVG(llm_ms), 0) AS avg_llm_ms,
                    COALESCE(AVG(citation_count > 0), 0) AS citation_rate
                FROM recent_requests
            """).fetchone()
            latencies = [
                row["total_ms"] for row in connection.execute(f"""
                    SELECT total_ms FROM request_logs
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT {self.METRICS_WINDOW_SIZE}
                """)
            ]
            feedback = connection.execute(f"""
                WITH recent_requests AS (
                    SELECT request_id FROM request_logs
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT {self.METRICS_WINDOW_SIZE}
                )
                SELECT COUNT(*) AS total, COALESCE(SUM(helpful = 1), 0) AS helpful
                FROM feedback
                WHERE request_id IN (SELECT request_id FROM recent_requests)
            """).fetchone()

        p50, p95 = self._percentiles(latencies)
        total_requests = summary["total_requests"]
        return {
            "total_requests": total_requests,
            "window_limit": self.METRICS_WINDOW_SIZE,
            "success_rate": round(summary["successful_requests"] / total_requests, 4) if total_requests else 0.0,
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "avg_retrieval_ms": round(summary["avg_retrieval_ms"], 2),
            "avg_llm_ms": round(summary["avg_llm_ms"], 2),
            "citation_rate": round(summary["citation_rate"], 4),
            "feedback_total": feedback["total"],
            "helpful_rate": round(feedback["helpful"] / feedback["total"], 4) if feedback["total"] else 0.0,
        }

    def failure_cases(self, limit: int = 100) -> list[dict]:
        """返回需要人工复核的请求，供反馈闭环和回归集整理使用。"""
        if limit <= 0 or limit > self.METRICS_WINDOW_SIZE:
            raise ValueError(f"limit 必须在 1 到 {self.METRICS_WINDOW_SIZE} 之间")

        with self._connect() as connection:
            rows = connection.execute("""
                SELECT
                    request_logs.request_id,
                    request_logs.session_id,
                    request_logs.query,
                    request_logs.intent,
                    request_logs.retrieved_count,
                    request_logs.citation_count,
                    request_logs.retrieval_ms,
                    request_logs.llm_ms,
                    request_logs.total_ms,
                    request_logs.status,
                    request_logs.error_message,
                    request_logs.created_at,
                    feedback.helpful AS feedback_helpful,
                    feedback.comment AS feedback_comment,
                    feedback.created_at AS feedback_created_at
                FROM request_logs
                LEFT JOIN feedback ON feedback.request_id = request_logs.request_id
                WHERE request_logs.status != 'success'
                   OR feedback.helpful = 0
                   OR request_logs.retrieved_count = 0
                   OR request_logs.citation_count = 0
                ORDER BY request_logs.created_at DESC, request_logs.rowid DESC
                LIMIT ?
            """, (limit,)).fetchall()

        cases = []
        for row in rows:
            reasons = []
            if row["status"] != "success":
                reasons.append("request_error")
            if row["feedback_helpful"] == 0:
                reasons.append("negative_feedback")
            if row["retrieved_count"] == 0:
                reasons.append("no_retrieval")
            if row["status"] == "success" and row["citation_count"] == 0:
                reasons.append("no_citation")

            cases.append({
                "request_id": row["request_id"],
                "session_id": row["session_id"],
                "query": row["query"],
                "intent": row["intent"],
                "retrieved_count": row["retrieved_count"],
                "citation_count": row["citation_count"],
                "retrieval_ms": row["retrieval_ms"],
                "llm_ms": row["llm_ms"],
                "total_ms": row["total_ms"],
                "status": row["status"],
                "error_message": row["error_message"],
                "created_at": row["created_at"],
                "feedback_helpful": bool(row["feedback_helpful"]) if row["feedback_helpful"] is not None else None,
                "feedback_comment": row["feedback_comment"] or "",
                "feedback_created_at": row["feedback_created_at"],
                "failure_reasons": reasons,
            })
        return cases

    @staticmethod
    def _percentiles(latencies: list[float]) -> tuple[float, float]:
        if not latencies:
            return 0.0, 0.0
        if len(latencies) == 1:
            return round(latencies[0], 2), round(latencies[0], 2)
        values = quantiles(latencies, n=100, method="inclusive")
        return round(values[49], 2), round(values[94], 2)
