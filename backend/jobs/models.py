from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def active_statuses(cls) -> tuple["JobStatus", ...]:
        return (cls.QUEUED, cls.RETRYING, cls.RUNNING)


@dataclass(slots=True)
class JobRecord:
    job_id: str
    owner_id: str
    status: JobStatus
    source_path: str
    input_manifest: list[dict[str, Any]]
    output_manifest: list[dict[str, Any]]
    parameters: dict[str, Any]
    total_files: int
    processed_files: int
    progress: float
    current_file: Optional[str]
    download_path: Optional[str]
    error: Optional[str]
    retry_count: int
    locked_by: Optional[str]
    locked_at: Optional[datetime]
    heartbeat_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    failed_at: Optional[datetime]


@dataclass(slots=True)
class JobEvent:
    event_id: int
    job_id: str
    created_at: datetime
    level: str
    message: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class JobCompletion:
    output_manifest: list[dict[str, Any]]
    download_path: Optional[str]
