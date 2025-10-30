from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

from backend.jobs.worker import ProgressReporter
from backend.jobs.repository import JobRepository
from backend.jobs.service import JobService
from backend.processors.analysis import AnalysisJobProcessor


class ConstantLabelService:
    async def analyse(self, pdf_path: Path) -> Tuple[dict[str, str], list[str]]:
        return {
            "model_name": 123,
            "voltage": None,
            "typ_batt_capacity_wh": 50,
            "typ_capacity_mah": 4000,
            "rated_capacity_mah": 3800,
            "rated_energy_wh": 48,
        }, ["done"]

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_processor_converts_field_values_to_strings(tmp_path: Path) -> None:
    repository = JobRepository(url=f"sqlite:///{tmp_path/'queue.db'}")
    service = JobService(repository=repository, storage_root=tmp_path / "jobs")
    processor = AnalysisJobProcessor(label_service_factory=ConstantLabelService)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    pdf_path = source_dir / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF")

    job = service.create_job(
        owner_id="alice",
        source_path=str(source_dir),
        files=[{"filename": "doc.pdf"}],
    )
    reporter = ProgressReporter(repository, service, "worker", repository.get_job(job.job_id))
    completion = await processor.run(repository.get_job(job.job_id), service.job_directory(job.job_id), reporter)
    assert completion.output_manifest[0]["model_name"] == "123"


@pytest.mark.asyncio
async def test_processor_raises_for_missing_file(tmp_path: Path) -> None:
    repository = JobRepository(url=f"sqlite:///{tmp_path/'queue.db'}")
    service = JobService(repository=repository, storage_root=tmp_path / "jobs")
    processor = AnalysisJobProcessor(label_service_factory=ConstantLabelService)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    # file intentionally missing

    pdf_path = source_dir / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF")

    job = service.create_job(
        owner_id="alice",
        source_path=str(source_dir),
        files=[{"filename": "doc.pdf"}],
    )

    (service.job_directory(job.job_id) / 'input' / 'doc.pdf').unlink()

    job_state = repository.get_job(job.job_id)
    reporter = ProgressReporter(repository, service, "worker", job_state)
    with pytest.raises(FileNotFoundError):
        await processor.run(job_state, service.job_directory(job.job_id), reporter)
