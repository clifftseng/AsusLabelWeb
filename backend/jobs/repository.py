from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .models import JobEvent, JobRecord, JobStatus

SQLITE_PREFIX = "sqlite:///"


def _sqlite_path(url: str) -> Path:
    if url == "sqlite:///:memory:":
        return Path(":memory:")
    if not url.startswith(SQLITE_PREFIX):
        raise ValueError(f"Unsupported URL for JobRepository: {url}")
    path = url[len(SQLITE_PREFIX) :]
    if path.startswith("/"):
        return Path(path)
    return Path(path).expanduser()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _deserialize_json(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _default_display_name(timestamp: datetime) -> str:
    local_dt = timestamp.astimezone()
    return local_dt.strftime("%m/%d %H:%M")


class JobRepository:
    """Persistence layer for job queue state built on top of SQLite."""

    def __init__(self, url: str, *, pragmas: Optional[dict[str, Any]] = None) -> None:
        self._path = _sqlite_path(url)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            ":memory:" if str(self._path) == ":memory:" else str(self._path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        if pragmas:
            for key, value in pragmas.items():
                self._conn.execute(f"PRAGMA {key} = {value}")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _transaction(self) -> Any:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    display_name TEXT,
                    input_manifest TEXT NOT NULL,
                    output_manifest TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    total_files INTEGER NOT NULL,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0,
                    current_file TEXT,
                    download_path TEXT,
                    error TEXT,
                    locked_by TEXT,
                    locked_at TEXT,
                    heartbeat_at TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    cancelled_at TEXT,
                    failed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                    ON jobs (status, created_at);

                CREATE INDEX IF NOT EXISTS idx_jobs_owner_created
                    ON jobs (owner_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_job_events_job_created
                    ON job_events (job_id, created_at);
                """
            )
            try:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN display_name TEXT")
            except sqlite3.OperationalError:
                pass

    def enqueue_job(self, *, owner_id: str, payload: dict[str, Any]) -> JobRecord:
        job_id = uuid4().hex
        dt_now = _utcnow()
        now = dt_now.isoformat()
        files = payload.get("files") or []
        total_files = int(payload.get("total_files") or len(files))
        display_name = payload.get("display_name")
        if not display_name:
            display_name = _default_display_name(dt_now)
        record = {
            "job_id": job_id,
            "owner_id": owner_id,
            "status": JobStatus.QUEUED.value,
            "source_path": payload.get("source_path", ""),
            "display_name": display_name,
            "input_manifest": _serialize_json(files),
            "output_manifest": _serialize_json([]),
            "parameters": _serialize_json(payload.get("parameters") or {}),
            "total_files": total_files,
            "processed_files": 0,
            "progress": 0.0,
            "current_file": None,
            "download_path": None,
            "error": None,
            "locked_by": None,
            "locked_at": None,
            "heartbeat_at": None,
            "version": 0,
            "retry_count": 0,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "cancelled_at": None,
            "failed_at": None,
        }
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO jobs (
                    job_id, owner_id, status, source_path, display_name,
                    input_manifest, output_manifest, parameters,
                    total_files, processed_files, progress,
                    current_file, download_path, error,
                    locked_by, locked_at, heartbeat_at, version,
                    retry_count, created_at, updated_at,
                    started_at, completed_at, cancelled_at, failed_at
                )
                VALUES (
                    :job_id, :owner_id, :status, :source_path, :display_name,
                    :input_manifest, :output_manifest, :parameters,
                    :total_files, :processed_files, :progress,
                    :current_file, :download_path, :error,
                    :locked_by, :locked_at, :heartbeat_at, :version,
                    :retry_count, :created_at, :updated_at,
                    :started_at, :completed_at, :cancelled_at, :failed_at
                )
                """,
                record,
            )
            self._append_event(
                cursor,
                job_id=job_id,
                level="info",
                message="Job queued",
                metadata={"total_files": total_files, "display_name": display_name},
            )

        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Job {job_id} not found")
        return self._row_to_job(row)

    def acquire_next_job(self, *, worker_id: str) -> Optional[JobRecord]:
        with self._transaction() as cursor:
            row = cursor.execute(
                """
                SELECT * FROM jobs
                WHERE status IN ('queued', 'retrying')
                ORDER BY created_at
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None

            job_id = row["job_id"]
            new_version = row["version"] + 1
            now = _utcnow().isoformat()
            updated = cursor.execute(
                """
                UPDATE jobs
                   SET status = :status,
                       version = :version,
                       locked_by = :worker_id,
                       locked_at = :now,
                       heartbeat_at = :now,
                       started_at = COALESCE(started_at, :now),
                       updated_at = :now
                 WHERE job_id = :job_id
                   AND version = :expected_version
                """,
                {
                    "status": JobStatus.RUNNING.value,
                    "version": new_version,
                    "worker_id": worker_id,
                    "now": now,
                    "job_id": job_id,
                    "expected_version": row["version"],
                },
            )
            if updated.rowcount != 1:
                return None

            self._append_event(
                cursor,
                job_id=job_id,
                level="info",
                message=f"Job claimed by {worker_id}",
                metadata={"worker_id": worker_id},
            )

        return self.get_job(job_id)

    def update_progress(
        self,
        *,
        job_id: str,
        worker_id: str,
        processed: int,
        total: int,
        progress: float,
        current_file: Optional[str],
        message: Optional[str] = None,
    ) -> JobRecord:
        now = _utcnow().isoformat()
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT locked_by FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Job {job_id} not found")
            if row["locked_by"] and row["locked_by"] != worker_id:
                raise PermissionError(
                    f"Job {job_id} is locked by {row['locked_by']}, worker {worker_id} cannot update."
                )
            cursor.execute(
                """
                UPDATE jobs
                   SET processed_files = :processed,
                       total_files = :total,
                       progress = :progress,
                       current_file = :current_file,
                       heartbeat_at = :now,
                       updated_at = :now
                 WHERE job_id = :job_id
                """,
                {
                    "processed": processed,
                    "total": total,
                    "progress": progress,
                    "current_file": current_file,
                    "now": now,
                    "job_id": job_id,
                },
            )
            if message:
                self._append_event(
                    cursor,
                    job_id=job_id,
                    level="info",
                    message=message,
                    metadata={
                        "processed": processed,
                        "total": total,
                        "current_file": current_file,
                    },
        )

        return self.get_job(job_id)

    def update_display_name(self, job_id: str, display_name: str) -> JobRecord:
        now = _utcnow().isoformat()
        with self._transaction() as cursor:
            updated = cursor.execute(
                """
                UPDATE jobs
                   SET display_name = :display_name,
                       updated_at = :now
                 WHERE job_id = :job_id
                """,
                {"display_name": display_name, "now": now, "job_id": job_id},
            )
            if updated.rowcount != 1:
                raise KeyError(f"Job {job_id} not found")
            self._append_event(
                cursor,
                job_id=job_id,
                level="info",
                message=f"Job renamed to {display_name}",
                metadata={"display_name": display_name},
            )

        return self.get_job(job_id)

    def delete_jobs(self, job_ids: list[str]) -> None:
        ids = [job_id for job_id in job_ids if job_id]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._transaction() as cursor:
            cursor.execute(
                f"DELETE FROM jobs WHERE job_id IN ({placeholders})",
                ids,
            )

    def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        output_manifest: list[dict[str, Any]],
        download_path: Optional[str],
    ) -> JobRecord:
        now = _utcnow().isoformat()
        with self._transaction() as cursor:
            locked = cursor.execute(
                "SELECT locked_by FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if locked is None:
                raise KeyError(f"Job {job_id} not found")
            if locked["locked_by"] and locked["locked_by"] != worker_id:
                raise PermissionError(
                    f"Job {job_id} is locked by {locked['locked_by']}, worker {worker_id} cannot complete."
                )
            cursor.execute(
                """
                UPDATE jobs
                   SET status = :status,
                       progress = 1.0,
                       processed_files = total_files,
                       output_manifest = :output_manifest,
                       download_path = :download_path,
                       error = NULL,
                       locked_by = NULL,
                       locked_at = NULL,
                       heartbeat_at = :now,
                       updated_at = :now,
                       completed_at = :now
                 WHERE job_id = :job_id
                """,
                {
                    "status": JobStatus.COMPLETED.value,
                    "output_manifest": _serialize_json(output_manifest),
                    "download_path": download_path,
                    "now": now,
                    "job_id": job_id,
                },
            )
            self._append_event(
                cursor,
                job_id=job_id,
                level="info",
                message="Job completed",
                metadata={"download_path": download_path},
            )

        return self.get_job(job_id)

    def fail_job(self, *, job_id: str, worker_id: str, error_message: str) -> JobRecord:
        now = _utcnow().isoformat()
        with self._transaction() as cursor:
            locked = cursor.execute(
                "SELECT locked_by FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if locked is None:
                raise KeyError(f"Job {job_id} not found")
            if locked["locked_by"] and locked["locked_by"] != worker_id:
                raise PermissionError(
                    f"Job {job_id} is locked by {locked['locked_by']}, worker {worker_id} cannot fail it."
                )
            cursor.execute(
                """
                UPDATE jobs
                   SET status = :status,
                       error = :error,
                       locked_by = NULL,
                       locked_at = NULL,
                       heartbeat_at = :now,
                       updated_at = :now,
                       failed_at = :now
                 WHERE job_id = :job_id
                """,
                {
                    "status": JobStatus.FAILED.value,
                    "error": error_message,
                    "now": now,
                    "job_id": job_id,
                },
            )
            self._append_event(
                cursor,
                job_id=job_id,
                level="error",
                message=error_message,
                metadata={"worker_id": worker_id},
            )

        return self.get_job(job_id)

    def cancel_job(self, job_id: str, *, reason: str, cancelled_by: Optional[str]) -> JobRecord:
        now = _utcnow().isoformat()
        with self._transaction() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                   SET status = :status,
                       error = :reason,
                       locked_by = NULL,
                       locked_at = NULL,
                       heartbeat_at = :now,
                       updated_at = :now,
                       cancelled_at = :now
                 WHERE job_id = :job_id
                """,
                {
                    "status": JobStatus.CANCELLED.value,
                    "reason": reason,
                    "now": now,
                    "job_id": job_id,
                },
            )
            self._append_event(
                cursor,
                job_id=job_id,
                level="warning",
                message=f"Job cancelled by {cancelled_by or 'system'}: {reason}",
                metadata={"cancelled_by": cancelled_by},
            )

        return self.get_job(job_id)

    def requeue_job(self, job_id: str, *, reason: str) -> JobRecord:
        now = _utcnow().isoformat()
        with self._transaction() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                   SET status = :status,
                       retry_count = retry_count + 1,
                       error = NULL,
                       locked_by = NULL,
                       locked_at = NULL,
                       heartbeat_at = NULL,
                       current_file = NULL,
                       processed_files = 0,
                       progress = 0,
                       updated_at = :now
                 WHERE job_id = :job_id
                """,
                {
                    "status": JobStatus.RETRYING.value,
                    "now": now,
                    "job_id": job_id,
                },
            )
            self._append_event(
                cursor,
                job_id=job_id,
                level="info",
                message=f"Job requeued: {reason}",
                metadata={"reason": reason},
            )

        return self.get_job(job_id)

    def list_events(self, job_id: str) -> list[JobEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM job_events
                 WHERE job_id = ?
                 ORDER BY created_at
                """,
                (job_id,),
            ).fetchall()
        events: list[JobEvent] = []
        for row in rows:
            events.append(
                JobEvent(
                    event_id=row["event_id"],
                    job_id=row["job_id"],
                    created_at=_parse_dt(row["created_at"]),
                    level=row["level"],
                    message=row["message"],
                    metadata=_deserialize_json(row["metadata"], {}),
                )
            )
        return events

    def append_event(
        self,
        job_id: str,
        *,
        level: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._transaction() as cursor:
            exists = cursor.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if exists is None:
                raise KeyError(f"Job {job_id} not found")
            self._append_event(
                cursor,
                job_id=job_id,
                level=level,
                message=message,
                metadata=metadata or {},
            )

    def list_jobs(
        self,
        *,
        owner_id: Optional[str] = None,
        statuses: Optional[list[JobStatus]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend([status.value for status in statuses])

        where_clause = ""
        if clauses:
            where_clause = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT * FROM jobs
             {where_clause}
             ORDER BY created_at DESC
             LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_job(row) for row in rows]

    def _append_event(
        self,
        cursor: sqlite3.Cursor,
        *,
        job_id: str,
        level: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO job_events (job_id, created_at, level, message, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                _utcnow().isoformat(),
                level,
                message,
                _serialize_json(metadata or {}),
            ),
        )

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        created_at = _parse_dt(row["created_at"])
        display_name = row["display_name"] or _default_display_name(created_at or _utcnow())
        return JobRecord(
            job_id=row["job_id"],
            owner_id=row["owner_id"],
            status=JobStatus(row["status"]),
            source_path=row["source_path"],
            display_name=display_name,
            input_manifest=_deserialize_json(row["input_manifest"], []),
            output_manifest=_deserialize_json(row["output_manifest"], []),
            parameters=_deserialize_json(row["parameters"], {}),
            total_files=row["total_files"],
            processed_files=row["processed_files"],
            progress=row["progress"],
            current_file=row["current_file"],
            download_path=row["download_path"],
            error=row["error"],
            retry_count=row["retry_count"],
            locked_by=row["locked_by"],
            locked_at=_parse_dt(row["locked_at"]),
            heartbeat_at=_parse_dt(row["heartbeat_at"]),
            created_at=created_at,
            updated_at=_parse_dt(row["updated_at"]),
            started_at=_parse_dt(row["started_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            cancelled_at=_parse_dt(row["cancelled_at"]),
            failed_at=_parse_dt(row["failed_at"]),
        )
