from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs.models import JobCompletion, JobStatus
from backend.jobs.repository import JobRepository
from backend.jobs.service import JobService
from backend.jobs.worker import JobWorker, ProgressReporter


class SuccessfulProcessor:
    async def run(self, job, job_dir: Path, reporter: ProgressReporter) -> JobCompletion:
        await reporter.report(processed=1, total=1, current_file="doc1.pdf", message="done")
        output = job_dir / "output" / "report.xlsx"
        output.write_text("dummy", encoding="utf-8")
        return JobCompletion(
            output_manifest=[{"filename": "doc1.pdf", "status": "ok"}],
            download_path=str(output),
        )


class FailingProcessor:
    async def run(self, job, job_dir: Path, reporter: ProgressReporter) -> JobCompletion:
        raise RuntimeError("Processing failed")


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    repo = JobRepository(url=f"sqlite:///{tmp_path/'queue.db'}")
    yield repo
    repo.close()


@pytest.fixture
def service(tmp_path: Path, repository: JobRepository) -> JobService:
    storage_root = tmp_path / "jobs"
    return JobService(repository=repository, storage_root=storage_root)


def create_job(service: JobService, source_dir: Path) -> str:
    job = service.create_job(
        owner_id="alice",
        source_path=str(source_dir),
        files=[{"filename": "doc1.pdf"}],
    )
    return job.job_id


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    (src / "doc1.pdf").write_text("pdf", encoding="utf-8")
    return src


@pytest.mark.asyncio
async def test_worker_processes_job(repository: JobRepository, service: JobService, source_dir: Path) -> None:
    job_id = create_job(service, source_dir)
    worker = JobWorker(repository=repository, service=service, processor=SuccessfulProcessor(), poll_interval=0.01)

    processed = await worker.run_once()
    assert processed is True

    job = repository.get_job(job_id)
    assert job.status is JobStatus.COMPLETED
    assert job.download_path


@pytest.mark.asyncio
async def test_worker_marks_job_failed(repository: JobRepository, service: JobService, source_dir: Path) -> None:
    job_id = create_job(service, source_dir)
    worker = JobWorker(repository=repository, service=service, processor=FailingProcessor(), poll_interval=0.01)

    processed = await worker.run_once()
    assert processed is True

    job = repository.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert "Processing failed" in (job.error or "")
