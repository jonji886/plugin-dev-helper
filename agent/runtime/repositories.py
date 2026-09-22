"""轻量 SQLite Repository：隔离 Runtime 与 Persistence。

只使用 SQLite（遵循 P0 约束，不引入 Redis / PostgreSQL / Kafka）。
保存的是恢复任务真正需要的 deterministic state，大对象（完整日志 / 全量 prompt）
用引用或摘要，不整体塞入运行时状态。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent.runtime.models import (
    ArtifactVersion,
    Checkpoint,
    FailureRecord,
    Task,
    TaskStep,
    TraceEvent,
)


class TaskRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._connect() as conn:
            # 兼容旧库：build_verified 列可能不存在（CREATE 仅对新库生效）
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN build_verified INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
                    current_step TEXT NOT NULL DEFAULT '', input TEXT NOT NULL DEFAULT '',
                    workspace TEXT NOT NULL DEFAULT '', artifact_version TEXT NOT NULL DEFAULT 'v0',
                    repair_attempt INTEGER NOT NULL DEFAULT 0, max_repair_attempts INTEGER NOT NULL DEFAULT 2,
                    build_verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS task_steps (
                    step_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    step_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
                    started_at TEXT NOT NULL DEFAULT '', completed_at TEXT NOT NULL DEFAULT '',
                    input_summary TEXT NOT NULL DEFAULT '', output_summary TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', artifact_version TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    status TEXT NOT NULL, current_step TEXT NOT NULL, artifact_version TEXT NOT NULL,
                    completed_steps_json TEXT NOT NULL DEFAULT '[]', repair_attempt INTEGER NOT NULL DEFAULT 0,
                    next_action TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_validation_result_ref TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    task_id TEXT NOT NULL, run_id TEXT NOT NULL, version TEXT NOT NULL,
                    parent_version TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, files_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (task_id, run_id, version)
                );
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    step_id TEXT NOT NULL DEFAULT '', artifact_version TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    duration_ms REAL NOT NULL DEFAULT 0, input_summary TEXT NOT NULL DEFAULT '',
                    output_summary TEXT NOT NULL DEFAULT '', error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS failure_records (
                    task_id TEXT NOT NULL, run_id TEXT NOT NULL, failure_stage TEXT NOT NULL,
                    failure_type TEXT NOT NULL, failure_code TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '', recoverable INTEGER NOT NULL DEFAULT 1,
                    recommended_action TEXT NOT NULL DEFAULT '', first_divergence INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    # ----- Task -----
    def save_task(self, task: Task) -> None:
        fields = task.to_dict()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tasks (
                    task_id, run_id, status, current_step, input, workspace, artifact_version,
                    repair_attempt, max_repair_attempts, build_verified, created_at, updated_at, last_error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    run_id=excluded.run_id, status=excluded.status, current_step=excluded.current_step,
                    input=excluded.input, workspace=excluded.workspace, artifact_version=excluded.artifact_version,
                    repair_attempt=excluded.repair_attempt, max_repair_attempts=excluded.max_repair_attempts,
                    build_verified=excluded.build_verified,
                    updated_at=excluded.updated_at, last_error=excluded.last_error""",
                (fields["task_id"], fields["run_id"], fields["status"], fields["current_step"],
                 fields["input"], fields["workspace"], fields["artifact_version"], fields["repair_attempt"],
                 fields["max_repair_attempts"], 1 if fields["build_verified"] else 0,
                 fields["created_at"], fields["updated_at"], fields["last_error"]),
            )

    def get_task(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return Task.from_dict(dict(row)) if row else None

    # ----- Step -----
    def save_step(self, step: TaskStep) -> None:
        fields = step.to_dict()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO task_steps (
                    step_id, task_id, run_id, step_type, status, started_at, completed_at,
                    input_summary, output_summary, error, artifact_version
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(step_id) DO UPDATE SET
                    status=excluded.status, started_at=excluded.started_at, completed_at=excluded.completed_at,
                    input_summary=excluded.input_summary, output_summary=excluded.output_summary,
                    error=excluded.error, artifact_version=excluded.artifact_version""",
                (fields["step_id"], fields["task_id"], fields["run_id"], fields["step_type"], fields["status"],
                 fields["started_at"], fields["completed_at"], fields["input_summary"], fields["output_summary"],
                 fields["error"], fields["artifact_version"]),
            )

    def list_steps(self, task_id: str) -> list[TaskStep]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY started_at, rowid", (task_id,)
            ).fetchall()
        return [TaskStep.from_dict(dict(r)) for r in rows]

    # ----- Checkpoint -----
    def save_checkpoint(self, cp: Checkpoint) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO checkpoints (
                    checkpoint_id, task_id, run_id, status, current_step, artifact_version,
                    completed_steps_json, repair_attempt, next_action, created_at,
                    last_validation_result_ref
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    status=excluded.status, current_step=excluded.current_step, artifact_version=excluded.artifact_version,
                    completed_steps_json=excluded.completed_steps_json, repair_attempt=excluded.repair_attempt,
                    next_action=excluded.next_action, last_validation_result_ref=excluded.last_validation_result_ref""",
                (cp.checkpoint_id, cp.task_id, cp.run_id, cp.status, cp.current_step, cp.artifact_version,
                 json.dumps(cp.completed_steps), cp.repair_attempt, cp.next_action, cp.created_at,
                 cp.last_validation_result_ref),
            )

    def latest_checkpoint(self, task_id: str) -> Checkpoint | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["completed_steps"] = json.loads(data.pop("completed_steps_json") or "[]")
        return Checkpoint.from_dict(data)

    # ----- Artifact Version -----
    def save_artifact_version(self, task_id: str, run_id: str, av: ArtifactVersion) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO artifact_versions (task_id, run_id, version, parent_version, reason, created_at, files_json)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(task_id, run_id, version) DO UPDATE SET
                    parent_version=excluded.parent_version, reason=excluded.reason,
                    created_at=excluded.created_at, files_json=excluded.files_json""",
                (task_id, run_id, av.version, av.parent_version, av.reason, av.created_at,
                 json.dumps(av.files)),
            )

    def get_artifact_version(self, task_id: str, run_id: str, version: str) -> ArtifactVersion | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE task_id = ? AND run_id = ? AND version = ?",
                (task_id, run_id, version),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["files"] = json.loads(data.pop("files_json") or "{}")
        return ArtifactVersion.from_dict(data)

    # ----- Trace -----
    def save_trace_event(self, event: TraceEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO trace_events (
                    event_id, task_id, run_id, event_type, timestamp, step_id, artifact_version, status, duration_ms,
                    input_summary, output_summary, error_type, error_message, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event.event_id, event.task_id, event.run_id, event.event_type, event.timestamp,
                 event.step_id, event.artifact_version, event.status, event.duration_ms, event.input_summary, event.output_summary,
                 event.error_type, event.error_message, json.dumps(event.metadata)),
            )

    def list_trace_events(self, task_id: str, run_id: str | None = None) -> list[TraceEvent]:
        with self._connect() as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM trace_events WHERE task_id = ? AND run_id = ? ORDER BY timestamp, rowid",
                    (task_id, run_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trace_events WHERE task_id = ? ORDER BY timestamp, rowid", (task_id,)
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            out.append(TraceEvent.from_dict(d))
        return out

    # ----- Failure -----
    def save_failure(self, fr: FailureRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO failure_records (
                    task_id, run_id, failure_stage, failure_type, failure_code, evidence,
                    recoverable, recommended_action, first_divergence, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (fr.task_id, fr.run_id, fr.failure_stage, fr.failure_type, fr.failure_code, fr.evidence,
                 1 if fr.recoverable else 0, fr.recommended_action, 1 if fr.first_divergence else 0, fr.created_at),
            )

    def list_failures(self, task_id: str) -> list[FailureRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM failure_records WHERE task_id = ? ORDER BY created_at, rowid",
                (task_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["recoverable"] = bool(d.pop("recoverable"))
            d["first_divergence"] = bool(d.pop("first_divergence"))
            out.append(FailureRecord.from_dict(d))
        return out
