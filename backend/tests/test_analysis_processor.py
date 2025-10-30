from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Tuple
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.jobs.models import JobRecord, JobStatus
from backend.processors.analysis import AnalysisJobProcessor


class FakeLabelService:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[Path] = []

    async def analyse(self, pdf_path: Path) -> Tuple[dict[str, str], list[str]]:
        self.calls.append(pdf_path)
        return {
            "model_name": "X1",
            "voltage": "7.6V",
            "typ_batt_capacity_wh": "50",
            "typ_capacity_mah": "6600",
            "rated_capacity_mah": "6400",
            "rated_energy_wh": "48",
        }, ["analysis complete"]

    async def aclose(self) -> None:
        self.closed = True


class DummyReporter:
    def __init__(self) -> None:
        self.events: list[tuple[int, int, str | None, str | None]] = []

    async def report(
        self,
        *,
        processed: int,
        total: int,
        current_file: str | None,
        message: str | None = None,
    ) -> JobRecord | None:
        self.events.append((processed, total, current_file, message))
        return None


@pytest.mark.asyncio
async def test_analysis_processor_generates_excel(tmp_path: Path) -> None:
    service = FakeLabelService()
    processor = AnalysisJobProcessor(label_service_factory=lambda: service)

    job_dir = tmp_path / "job"
    input_dir = job_dir / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "doc1.pdf").write_bytes(b"%PDF-1.4")

    now = datetime.now(timezone.utc)
    job = JobRecord(
        job_id="job1",
        owner_id="alice",
        status=JobStatus.QUEUED,
        source_path=str(tmp_path),
        input_manifest=[{"filename": "doc1.pdf"}],
        output_manifest=[],
        parameters={},
        total_files=1,
        processed_files=0,
        progress=0.0,
        current_file=None,
        download_path=None,
        error=None,
        retry_count=0,
        locked_by=None,
        locked_at=None,
        heartbeat_at=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
        failed_at=None,
    )

    reporter = DummyReporter()
    result = await processor.run(job, job_dir, reporter)

    assert result.download_path is not None
    assert Path(result.download_path).exists()
    assert result.output_manifest[0]["filename"] == "doc1.pdf"
    assert service.closed is True
    assert service.calls


@pytest.mark.asyncio
async def test_analysis_processor_handles_empty_manifest(tmp_path: Path) -> None:
    processor = AnalysisJobProcessor(label_service_factory=lambda: FakeLabelService())
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    now = datetime.now(timezone.utc)
    job = JobRecord(
        job_id="job2",
        owner_id="alice",
        status=JobStatus.QUEUED,
        source_path=str(tmp_path),
        input_manifest=[],
        output_manifest=[],
        parameters={},
        total_files=0,
        processed_files=0,
        progress=0.0,
        current_file=None,
        download_path=None,
        error=None,
        retry_count=0,
        locked_by=None,
        locked_at=None,
        heartbeat_at=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
        failed_at=None,
    )

    reporter = DummyReporter()
    result = await processor.run(job, job_dir, reporter)

    assert result.download_path is None
    assert result.output_manifest == []
    assert any(msg == "No PDF files selected for analysis." for *_, msg in reporter.events)
