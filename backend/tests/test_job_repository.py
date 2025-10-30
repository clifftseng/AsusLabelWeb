from __future__ import annotations

from pathlib import Path
import sys
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from typing import Any

import pytest

from backend.jobs.models import JobStatus
from backend.jobs.repository import JobRepository


@pytest.fixture
def repo(tmp_path: Path) -> JobRepository:
    db_path = tmp_path / "queue.db"
    repo = JobRepository(url=f"sqlite:///{db_path}")
    yield repo
    repo.close()


def sample_payload(files: list[str] | None = None) -> dict[str, Any]:
    files = files or ["alpha.pdf", "bravo.pdf"]
    return {
        "source_path": "D:/shared/input",
        "files": [{"id": idx + 1, "filename": name} for idx, name in enumerate(files)],
        "parameters": {"note": "unit-test"},
    }


def test_enqueue_and_fetch_job(repo: JobRepository) -> None:
    created = repo.enqueue_job(owner_id="alice", payload=sample_payload())
    fetched = repo.get_job(created.job_id)

    assert fetched.job_id == created.job_id
    assert fetched.status is JobStatus.QUEUED
    assert fetched.owner_id == "alice"
    assert fetched.total_files == 2
    assert fetched.input_manifest[0]["filename"] == "alpha.pdf"
    assert fetched.retry_count == 0


def test_acquire_next_job_claims_oldest(repo: JobRepository) -> None:
    first = repo.enqueue_job(owner_id="alice", payload=sample_payload(["1.pdf"]))
    second = repo.enqueue_job(owner_id="bob", payload=sample_payload(["2.pdf"]))

    claimed = repo.acquire_next_job(worker_id="worker-1")
    assert claimed.job_id == first.job_id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.locked_by == "worker-1"

    # Another worker can claim the next job
    second_claim = repo.acquire_next_job(worker_id="worker-2")
    assert second_claim.job_id == second.job_id
    assert second_claim.locked_by == "worker-2"

    assert repo.acquire_next_job(worker_id="worker-3") is None


def test_progress_updates_and_events(repo: JobRepository) -> None:
    job = repo.enqueue_job(owner_id="alice", payload=sample_payload())
    claimed = repo.acquire_next_job(worker_id="worker-1")
    assert claimed.job_id == job.job_id

    repo.update_progress(
        job_id=job.job_id,
        worker_id="worker-1",
        processed=1,
        total=2,
        progress=0.5,
        current_file="alpha.pdf",
        message="Rendering pages",
    )

    refreshed = repo.get_job(job.job_id)
    assert refreshed.processed_files == 1
    assert refreshed.total_files == 2
    assert refreshed.current_file == "alpha.pdf"
    assert refreshed.progress == pytest.approx(0.5)

    events = repo.list_events(job.job_id)
    assert events[-1].message == "Rendering pages"
    assert events[-1].level == "info"


def test_fail_and_retry(repo: JobRepository) -> None:
    job = repo.enqueue_job(owner_id="alice", payload=sample_payload())
    repo.acquire_next_job(worker_id="worker-1")

    repo.fail_job(job_id=job.job_id, worker_id="worker-1", error_message="Boom!")

    failed = repo.get_job(job.job_id)
    assert failed.status is JobStatus.FAILED
    assert failed.error == "Boom!"

    # Requeue the job for retry
    retried = repo.requeue_job(job.job_id, reason="Retry test")
    assert retried.status is JobStatus.RETRYING
    assert retried.retry_count == 1
    assert repo.acquire_next_job(worker_id="worker-2").job_id == job.job_id


def test_cancel_job_append_event(repo: JobRepository) -> None:
    job = repo.enqueue_job(owner_id="alice", payload=sample_payload())
    repo.cancel_job(job.job_id, reason="User request", cancelled_by="alice")

    cancelled = repo.get_job(job.job_id)
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.error == "User request"

    events = repo.list_events(job.job_id)
    assert events[-1].level == "warning"
    assert "cancelled" in events[-1].message.lower()


def test_list_jobs_supports_filters(repo: JobRepository) -> None:
    first = repo.enqueue_job(owner_id="alice", payload=sample_payload(["a.pdf"]))
    repo.enqueue_job(owner_id="bob", payload=sample_payload(["b.pdf"]))

    jobs_for_alice = repo.list_jobs(owner_id="alice")
    assert [job.job_id for job in jobs_for_alice] == [first.job_id]

    all_jobs = repo.list_jobs()
    assert len(all_jobs) == 2



def test_append_event_records_message(repo: JobRepository) -> None:
    job = repo.enqueue_job(owner_id="alice", payload=sample_payload(["solo.pdf"]))
    repo.append_event(job.job_id, level="info", message="Hello")
    events = repo.list_events(job.job_id)
    assert events[-1].message == "Hello"
    assert events[-1].level == "info"
