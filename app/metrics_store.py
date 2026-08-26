"""SQLite storage for request telemetry, feedback and badcase promotion."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import quantiles
from typing import Any


class MetricsStore:
    METRICS_WINDOW_SIZE = 1000

    def __init__(self, database_path: Path, evaluation_dataset_path: Path | None = None):
        self.database_path = Path(database_path)
        self.evaluation_dataset_path = evaluation_dataset_path or Path("eval/regression_cases.json")
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
                    request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, query TEXT NOT NULL,
                    intent TEXT NOT NULL DEFAULT '', rewritten_query TEXT NOT NULL DEFAULT '',
                    retrieved_count INTEGER NOT NULL DEFAULT 0, citation_count INTEGER NOT NULL DEFAULT 0,
                    retrieved_documents_json TEXT NOT NULL DEFAULT '[]', retrieval_ms REAL NOT NULL DEFAULT 0,
                    llm_ms REAL NOT NULL DEFAULT 0, total_ms REAL NOT NULL DEFAULT 0, status TEXT NOT NULL,
                    error_message TEXT, answer TEXT NOT NULL DEFAULT '', prompt_name TEXT NOT NULL DEFAULT 'developer_qa',
                    prompt_version TEXT NOT NULL DEFAULT 'v1', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                    model_role TEXT NOT NULL DEFAULT '', route_reason TEXT NOT NULL DEFAULT '', image_count INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, helpful INTEGER NOT NULL,
                    rating TEXT NOT NULL DEFAULT 'positive', reason TEXT NOT NULL DEFAULT '', comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (request_id) REFERENCES request_logs(request_id)
                );
                CREATE TABLE IF NOT EXISTS badcases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'NEW', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TEXT, promoted_at TEXT, FOREIGN KEY (request_id) REFERENCES request_logs(request_id)
                );
            """)
            self._ensure_columns(connection, "request_logs", {
                "rewritten_query": "TEXT NOT NULL DEFAULT ''", "retrieved_documents_json": "TEXT NOT NULL DEFAULT '[]'",
                "answer": "TEXT NOT NULL DEFAULT ''", "prompt_name": "TEXT NOT NULL DEFAULT 'developer_qa'",
                "prompt_version": "TEXT NOT NULL DEFAULT 'v1'", "provider": "TEXT NOT NULL DEFAULT ''",
                "model": "TEXT NOT NULL DEFAULT ''", "model_role": "TEXT NOT NULL DEFAULT ''",
                "route_reason": "TEXT NOT NULL DEFAULT ''", "image_count": "INTEGER NOT NULL DEFAULT 0",
                "input_tokens": "INTEGER NOT NULL DEFAULT 0", "output_tokens": "INTEGER NOT NULL DEFAULT 0",
                "total_tokens": "INTEGER NOT NULL DEFAULT 0", "estimated_cost": "REAL NOT NULL DEFAULT 0",
            })
            self._ensure_columns(connection, "feedback", {
                "rating": "TEXT NOT NULL DEFAULT 'positive'", "reason": "TEXT NOT NULL DEFAULT ''",
            })
            connection.execute("DELETE FROM feedback WHERE id NOT IN (SELECT MAX(id) FROM feedback GROUP BY request_id)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_request_id ON feedback(request_id)")

    @staticmethod
    def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def record_request(self, record: dict[str, Any]) -> None:
        fields = {
            "request_id": record["request_id"], "session_id": record.get("session_id", ""), "query": record.get("query", ""),
            "intent": record.get("intent", ""), "rewritten_query": record.get("rewritten_query", ""),
            "retrieved_count": record.get("retrieved_count", 0), "citation_count": record.get("citation_count", 0),
            "retrieved_documents_json": json.dumps(record.get("retrieved_documents", []), ensure_ascii=False),
            "retrieval_ms": record.get("retrieval_ms", 0.0), "llm_ms": record.get("llm_ms", 0.0),
            "total_ms": record.get("total_ms", 0.0), "status": record.get("status", "success"),
            "error_message": record.get("error_message"), "answer": record.get("answer", ""),
            "prompt_name": record.get("prompt_name", "developer_qa"), "prompt_version": record.get("prompt_version", "v1"),
            "provider": record.get("provider", ""), "model": record.get("model", ""),
            "model_role": record.get("model_role", ""), "route_reason": record.get("route_reason", ""),
            "image_count": record.get("image_count", 0),
            "input_tokens": record.get("input_tokens", 0), "output_tokens": record.get("output_tokens", 0),
            "total_tokens": record.get("total_tokens", 0), "estimated_cost": record.get("estimated_cost", 0.0),
        }
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{column}=excluded.{column}" for column in fields if column != "request_id")
        with self._connect() as connection:
            connection.execute(f"INSERT INTO request_logs ({columns}) VALUES ({placeholders}) ON CONFLICT(request_id) DO UPDATE SET {updates}", tuple(fields.values()))

    def record_feedback(self, request_id: str, helpful: bool, comment: str = "", reason: str = "") -> bool:
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM request_logs WHERE request_id = ?", (request_id,)).fetchone()
            if not exists:
                return False
            rating = "positive" if helpful else "negative"
            connection.execute("""INSERT INTO feedback (request_id, helpful, rating, reason, comment) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET helpful=excluded.helpful, rating=excluded.rating,
                reason=excluded.reason, comment=excluded.comment, created_at=CURRENT_TIMESTAMP""",
                (request_id, int(helpful), rating, reason, comment))
            if not helpful:
                connection.execute("INSERT OR IGNORE INTO badcases (request_id) VALUES (?)", (request_id,))
        return True

    def metrics(self) -> dict:
        with self._connect() as connection:
            summary = connection.execute(f"""WITH recent_requests AS
                (SELECT rowid, * FROM request_logs ORDER BY created_at DESC, rowid DESC LIMIT {self.METRICS_WINDOW_SIZE})
                SELECT COUNT(*) total_requests, COALESCE(SUM(status='success'),0) successful_requests,
                COALESCE(AVG(retrieval_ms),0) avg_retrieval_ms, COALESCE(AVG(llm_ms),0) avg_llm_ms,
                COALESCE(AVG(citation_count > 0),0) citation_rate, COALESCE(SUM(total_tokens),0) total_tokens,
                COALESCE(SUM(estimated_cost),0) estimated_cost FROM recent_requests""").fetchone()
            latencies = [row["total_ms"] for row in connection.execute(f"SELECT total_ms FROM request_logs ORDER BY created_at DESC, rowid DESC LIMIT {self.METRICS_WINDOW_SIZE}")]
            feedback = connection.execute(f"""WITH recent_requests AS
                (SELECT request_id FROM request_logs ORDER BY created_at DESC, rowid DESC LIMIT {self.METRICS_WINDOW_SIZE})
                SELECT COUNT(*) total, COALESCE(SUM(helpful=1),0) helpful FROM feedback
                WHERE request_id IN (SELECT request_id FROM recent_requests)""").fetchone()
            role_rows = connection.execute(f"""WITH recent_requests AS
                (SELECT * FROM request_logs ORDER BY created_at DESC, rowid DESC LIMIT {self.METRICS_WINDOW_SIZE})
                SELECT COALESCE(NULLIF(model_role, ''), 'unknown') model_role,
                COUNT(*) total_requests, COALESCE(SUM(status='success'),0) successful_requests,
                COALESCE(AVG(total_ms),0) avg_total_ms, COALESCE(AVG(llm_ms),0) avg_llm_ms,
                COALESCE(SUM(total_tokens),0) total_tokens, COALESCE(SUM(estimated_cost),0) estimated_cost
                FROM recent_requests GROUP BY COALESCE(NULLIF(model_role, ''), 'unknown')""").fetchall()
        p50, p95 = self._percentiles(latencies)
        total = summary["total_requests"]
        successful = summary["successful_requests"]
        by_role = {}
        for row in role_rows:
            role_total = row["total_requests"]
            role_successful = row["successful_requests"]
            by_role[row["model_role"]] = {
                "total_requests": role_total,
                "successful_requests": role_successful,
                "failed_requests": role_total - role_successful,
                "failure_rate": round((role_total - role_successful) / role_total, 4) if role_total else 0.0,
                "avg_total_ms": round(row["avg_total_ms"], 2),
                "avg_llm_ms": round(row["avg_llm_ms"], 2),
                "total_tokens": row["total_tokens"],
                "estimated_cost": round(row["estimated_cost"], 8),
            }
        return {"total_requests": total, "window_limit": self.METRICS_WINDOW_SIZE,
                "successful_requests": successful, "failed_requests": total - successful,
                "success_rate": round(successful / total, 4) if total else 0.0,
                "failure_rate": round((total - successful) / total, 4) if total else 0.0,
                "p50_latency_ms": p50, "p95_latency_ms": p95,
                "avg_retrieval_ms": round(summary["avg_retrieval_ms"], 2), "avg_llm_ms": round(summary["avg_llm_ms"], 2),
                "citation_rate": round(summary["citation_rate"], 4), "total_tokens": summary["total_tokens"],
                "estimated_cost": round(summary["estimated_cost"], 8), "feedback_total": feedback["total"],
                "helpful_rate": round(feedback["helpful"] / feedback["total"], 4) if feedback["total"] else 0.0,
                "by_model_role": by_role}

    def failure_cases(self, limit: int = 100) -> list[dict]:
        if limit <= 0 or limit > self.METRICS_WINDOW_SIZE:
            raise ValueError(f"limit 必须在 1 到 {self.METRICS_WINDOW_SIZE} 之间")
        with self._connect() as connection:
            rows = connection.execute("""SELECT r.*, f.helpful feedback_helpful, f.reason feedback_reason,
                f.comment feedback_comment, f.created_at feedback_created_at FROM request_logs r
                LEFT JOIN feedback f ON f.request_id=r.request_id
                WHERE r.status!='success' OR f.helpful=0 OR r.retrieved_count=0 OR (r.status='success' AND r.citation_count=0)
                ORDER BY r.created_at DESC, r.rowid DESC LIMIT ?""", (limit,)).fetchall()
        cases = []
        for row in rows:
            reasons = []
            if row["status"] != "success": reasons.append("request_error")
            if row["feedback_helpful"] == 0: reasons.append("negative_feedback")
            if row["retrieved_count"] == 0: reasons.append("no_retrieval")
            if row["status"] == "success" and row["citation_count"] == 0: reasons.append("no_citation")
            cases.append({"request_id": row["request_id"], "trace_id": row["request_id"], "session_id": row["session_id"],
                "query": row["query"], "answer": row["answer"], "intent": row["intent"], "retrieved_count": row["retrieved_count"],
                "citation_count": row["citation_count"], "retrieved_documents": json.loads(row["retrieved_documents_json"] or "[]"),
                "retrieval_ms": row["retrieval_ms"], "llm_ms": row["llm_ms"], "total_ms": row["total_ms"],
                "status": row["status"], "error_message": row["error_message"], "model": row["model"], "prompt_version": row["prompt_version"],
                "provider": row["provider"], "model_role": row["model_role"],
                "image_count": row["image_count"], "created_at": row["created_at"],
                "feedback_helpful": bool(row["feedback_helpful"]) if row["feedback_helpful"] is not None else None,
                "feedback_reason": row["feedback_reason"] or "", "feedback_comment": row["feedback_comment"] or "",
                "feedback_created_at": row["feedback_created_at"], "failure_reasons": reasons})
        return cases

    def list_badcases(self, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT b.id, b.status, b.created_at badcase_created_at, r.request_id,
                r.query, r.answer, r.prompt_version, r.model, r.provider, r.created_at, f.reason feedback_reason,
                f.comment feedback_comment FROM badcases b JOIN request_logs r ON r.request_id=b.request_id
                LEFT JOIN feedback f ON f.request_id=r.request_id ORDER BY b.created_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def promote_badcase(self, badcase_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("""SELECT b.*, r.query, r.retrieved_documents_json, r.request_id, f.reason
                FROM badcases b JOIN request_logs r ON r.request_id=b.request_id LEFT JOIN feedback f ON f.request_id=r.request_id
                WHERE b.id=?""", (badcase_id,)).fetchone()
            if not row: return None
            case = {"id": f"feedback_{row['request_id']}", "question": row["query"], "expected_keywords": [],
                    "expected_sources": json.loads(row["retrieved_documents_json"] or "[]"), "expected_answer": "",
                    "source": "user_feedback", "feedback_reason": row["reason"] or ""}
            path = self.evaluation_dataset_path
            path.parent.mkdir(parents=True, exist_ok=True)
            try: dataset = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            except json.JSONDecodeError: dataset = []
            if not any(item.get("id") == case["id"] for item in dataset):
                dataset.append(case)
                path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("UPDATE badcases SET status='PROMOTED', promoted_at=? WHERE id=?", (now, badcase_id))
            return case

    def update_badcase_status(self, badcase_id: int, status: str) -> bool:
        if status not in {"NEW", "REVIEWED", "PROMOTED", "IGNORED"}:
            raise ValueError("无效的 badcase 状态")
        with self._connect() as connection:
            cursor = connection.execute("UPDATE badcases SET status=?, reviewed_at=? WHERE id=?",
                                        (status, datetime.now(timezone.utc).isoformat(), badcase_id))
            return cursor.rowcount > 0

    @staticmethod
    def _percentiles(latencies: list[float]) -> tuple[float, float]:
        if not latencies: return 0.0, 0.0
        if len(latencies) == 1: return round(latencies[0], 2), round(latencies[0], 2)
        values = quantiles(latencies, n=100, method="inclusive")
        return round(values[49], 2), round(values[94], 2)
