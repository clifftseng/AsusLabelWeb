from __future__ import annotations

from pathlib import Path

import pytest

from backend.jobs.repository import JobRepository
from backend.jobs.service import JobService
from backend.jobs.worker import JobWorker, ProgressReporter, JobProcessor
from backend.jobs.models import JobCompletion, JobRecord, JobStatus


class RecordingProcessor(JobProcessor):
    def __init__(self) -> None:
        self.processed: list[str] = []

    async def run(
        self,
        job: JobRecord,
        job_dir: Path,
        reporter: ProgressReporter,
    ) -> JobCompletion:
        await reporter.report(
            processed=job.total_files,
            total=job.total_files,
            current_file=None,
            message=f"Processed {job.job_id}",
        )
        self.processed.append(job.job_id)
        return JobCompletion(
            output_manifest=[{"filename": item["filename"], "status": "ok"} for item in job.input_manifest],
            download_path=None,
        )


def _create_sample_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n%EOF")


@pytest.mark.asyncio
async def test_worker_respects_fifo_order(tmp_path: Path) -> None:
    repo = JobRepository(url=f"sqlite:///{tmp_path/'queue.db'}")
    service = JobService(repository=repo, storage_root=tmp_path / "jobs")
    processor = RecordingProcessor()
    worker = JobWorker(repository=repo, service=service, processor=processor, poll_interval=0.01)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _create_sample_pdf(source_dir / "a.pdf")
    _create_sample_pdf(source_dir / "b.pdf")

    first = service.create_job(
        owner_id="alice",
        source_path=str(source_dir),
        files=[{"filename": "a.pdf"}],
    )
    second = service.create_job(
        owner_id="bob",
        source_path=str(source_dir),
        files=[{"filename": "b.pdf"}],
    )

    await worker.run_once()
    await worker.run_once()

    job_a = repo.get_job(first.job_id)
    job_b = repo.get_job(second.job_id)
    assert job_a.status is JobStatus.COMPLETED
    assert job_b.status is JobStatus.COMPLETED
    assert processor.processed == [first.job_id, second.job_id]


def test_list_jobs_filters_by_owner(tmp_path: Path) -> None:
    repo = JobRepository(url=f"sqlite:///{tmp_path/'queue.db'}")
    service = JobService(repository=repo, storage_root=tmp_path / "jobs")

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    _create_sample_pdf(source_dir / "one.pdf")
    _create_sample_pdf(source_dir / "two.pdf")

    service.create_job(
        owner_id="alice",
        source_path=str(source_dir),
        files=[{"filename": "one.pdf"}],
    )
    service.create_job(
        owner_id="bob",
        source_path=str(source_dir),
        files=[{"filename": "two.pdf"}],
    )

    alice_jobs = repo.list_jobs(owner_id="alice")
    bob_jobs = repo.list_jobs(owner_id="bob")
    assert len(alice_jobs) == 1
    assert len(bob_jobs) == 1
    assert alice_jobs[0].owner_id == "alice"
    assert bob_jobs[0].owner_id == "bob"
