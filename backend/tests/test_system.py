from __future__ import annotations

from pathlib import Path
import sys
from typing import AsyncIterator, Tuple

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app
from backend.settings import AppSettings
from backend.jobs.worker import JobWorker, ProgressReporter, JobProcessor
from backend.jobs.models import JobRecord, JobCompletion

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class SimpleProcessor(JobProcessor):
    async def run(self, job: JobRecord, job_dir: Path, reporter: ProgressReporter) -> JobCompletion:
        await reporter.report(
            processed=job.total_files,
            total=job.total_files,
            current_file=None,
            message="done",
        )
        output = job_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        report_path = output / "result.xlsx"
        report_path.write_text("ok", encoding="utf-8")
        return JobCompletion(
            output_manifest=[{"filename": item["filename"], "status": "ok"} for item in job.input_manifest],
            download_path=str(report_path),
        )


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[Tuple[httpx.AsyncClient, object]]:
    settings = AppSettings(
        job_queue_url=f"sqlite:///{tmp_path/'queue.db'}",
        job_storage_root=tmp_path / "jobs",
        job_max_workers=0,
    )
    app = create_app(settings=settings)
    app.state.processor_factory = lambda: SimpleProcessor()  # type: ignore[attr-defined]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True) as async_client:
        yield async_client, app


async def test_end_to_end_processing_flow(client: Tuple[httpx.AsyncClient, object], tmp_path: Path) -> None:
    async_client, app = client
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "doc.pdf").write_bytes(b"%PDF-1.4\n%EOF")

    create_response = await async_client.post(
        "/api/jobs/",
        json={
            "owner_id": "alice",
            "source_path": str(source_dir),
            "files": [{"filename": "doc.pdf"}],
        },
    )
    job_id = create_response.json()["job_id"]

    repo = app.state.job_repository  # type: ignore[attr-defined]
    service = app.state.job_service  # type: ignore[attr-defined]
    worker = JobWorker(repository=repo, service=service, processor=SimpleProcessor(), poll_interval=0.01)
    await worker.run_once()

    detail_response = await async_client.get(f"/api/jobs/{job_id}", params={"owner_id": "alice"})
    detail = detail_response.json()
    assert detail["status"] == "completed"
    assert detail["output_manifest"][0]["status"] == "ok"
