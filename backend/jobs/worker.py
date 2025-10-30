from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from uuid import uuid4

from .models import JobCompletion, JobRecord
from .repository import JobRepository
from .service import JobService

logger = logging.getLogger(__name__)


class JobProcessor(Protocol):
    async def run(
        self,
        job: JobRecord,
        job_dir: Path,
        reporter: "ProgressReporter",
    ) -> JobCompletion: ...


@dataclass(slots=True)
class ProgressUpdate:
    processed: int
    total: int
    current_file: Optional[str]
    message: Optional[str]


class ProgressReporter:
    def __init__(
        self,
        repository: JobRepository,
        service: JobService,
        worker_id: str,
        job: JobRecord,
    ) -> None:
        self._repository = repository
        self._service = service
        self._worker_id = worker_id
        self._job = job

    async def report(
        self,
        *,
        processed: int,
        total: int,
        current_file: Optional[str],
        message: Optional[str] = None,
    ) -> JobRecord:
        progress = 0.0 if total == 0 else processed / max(total, 1)
        updated = await asyncio.to_thread(
            self._repository.update_progress,
            job_id=self._job.job_id,
            worker_id=self._worker_id,
            processed=processed,
            total=total,
            progress=progress,
            current_file=current_file,
            message=message,
        )
        await asyncio.to_thread(self._service.refresh_status_snapshot, updated)
        return updated


class JobWorker:
    """Asynchronous worker that pulls jobs from the repository and executes a processor."""

    def __init__(
        self,
        *,
        repository: JobRepository,
        service: JobService,
        processor: JobProcessor,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
    ) -> None:
        self.repository = repository
        self.service = service
        self.processor = processor
        self.worker_id = worker_id or f"worker-{uuid4().hex[:8]}"
        self.poll_interval = poll_interval
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            processed = await self.run_once()
            if not processed:
                await asyncio.sleep(self.poll_interval)

    async def run_once(self) -> bool:
        job = await asyncio.to_thread(
            self.repository.acquire_next_job, worker_id=self.worker_id
        )
        if not job:
            return False

        reporter = ProgressReporter(self.repository, self.service, self.worker_id, job)
        job_dir = self.service.job_directory(job.job_id)

        try:
            completion = await self.processor.run(job=job, job_dir=job_dir, reporter=reporter)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Job %s failed: %s", job.job_id, exc)
            await asyncio.to_thread(
                self.repository.fail_job,
                job_id=job.job_id,
                worker_id=self.worker_id,
                error_message=str(exc),
            )
            failed = await asyncio.to_thread(self.repository.get_job, job.job_id)
            self.service.refresh_status_snapshot(failed)
            return True

        await asyncio.to_thread(
            self.repository.complete_job,
            job_id=job.job_id,
            worker_id=self.worker_id,
            output_manifest=completion.output_manifest,
            download_path=completion.download_path,
        )
        completed = await asyncio.to_thread(self.repository.get_job, job.job_id)
        self.service.refresh_status_snapshot(completed)
        return True
