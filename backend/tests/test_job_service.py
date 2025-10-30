from __future__ import annotations

from pathlib import Path
import shutil
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs.repository import JobRepository
from backend.jobs.service import JobService
from backend.jobs.models import JobStatus


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    for name in ("doc1.pdf", "doc2.pdf"):
        (src / name).write_bytes(b"%PDF-1.5\n%%Test content\n")
    return src


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    db_path = tmp_path / "queue.db"
    repo = JobRepository(url=f"sqlite:///{db_path}")
    yield repo
    repo.close()


@pytest.fixture
def service(tmp_path: Path, repository: JobRepository) -> JobService:
    storage_root = tmp_path / "jobs"
    return JobService(
        repository=repository,
        storage_root=storage_root,
    )


def test_create_job_copies_files(service: JobService, source_dir: Path, repository: JobRepository) -> None:
    job = service.create_job(
        owner_id="alice",
        source_path=str(source_dir),
        files=[{"filename": "doc1.pdf"}, {"filename": "doc2.pdf"}],
        parameters={"priority": "normal"},
    )

    assert job.status is JobStatus.QUEUED
    job_dir = service.job_directory(job.job_id)
    assert (job_dir / "input" / "doc1.pdf").exists()
    assert (job_dir / "input" / "doc2.pdf").exists()
    assert (job_dir / "status.json").exists()

    stored = repository.get_job(job.job_id)
    assert stored.total_files == 2
    assert stored.input_manifest[0]["filename"] == "doc1.pdf"


def test_create_job_missing_file_raises(service: JobService, source_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        service.create_job(
            owner_id="alice",
            source_path=str(source_dir),
            files=[{"filename": "missing.pdf"}],
        )


def test_cleanup_job_inputs(service: JobService, source_dir: Path) -> None:
    job = service.create_job(
        owner_id="alice",
        source_path=str(source_dir),
        files=[{"filename": "doc1.pdf"}],
    )
    job_dir = service.job_directory(job.job_id)
    working = job_dir / "input"
    assert any(working.iterdir())

    service.cleanup_inputs(job.job_id)
    assert not any(working.glob("*.pdf"))
