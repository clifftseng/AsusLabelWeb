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


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[Tuple[httpx.AsyncClient, object]]:
    settings = AppSettings(
        job_queue_url=f"sqlite:///{tmp_path/'queue.db'}",
        job_storage_root=tmp_path / "jobs",
        job_max_workers=0,
    )
    app = create_app(settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=True,
    ) as async_client:
        yield async_client, app


def _create_sample_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n%EOF")


async def test_job_lifecycle_endpoints(client: Tuple[httpx.AsyncClient, object], tmp_path: Path) -> None:
    async_client, app = client
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _create_sample_pdf(source_dir / "doc1.pdf")

    create_response = await async_client.post(
        "/api/jobs/",
        json={
            "owner_id": "alice",
            "source_path": str(source_dir),
            "files": [{"filename": "doc1.pdf"}],
        },
    )
    assert create_response.status_code == 201
    job_summary = create_response.json()
    job_id = job_summary["job_id"]

    repo = app.state.job_repository  # type: ignore[attr-defined]
    service = app.state.job_service  # type: ignore[attr-defined]

    claimed = repo.acquire_next_job(worker_id="worker-1")
    assert claimed is not None
    job_dir = service.job_directory(job_id)
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "analysis.xlsx"
    report_path.write_text("ok", encoding="utf-8")
    repo.complete_job(
        job_id=job_id,
        worker_id="worker-1",
        output_manifest=[{"filename": "doc1.pdf", "status": "ok"}],
        download_path=str(report_path),
    )

    detail_response = await async_client.get(f"/api/jobs/{job_id}", params={"owner_id": "alice"})
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["status"] == "completed"
    assert detail["output_manifest"][0]["filename"] == "doc1.pdf"

    download_response = await async_client.get(
        f"/api/jobs/{job_id}/download",
        params={"owner_id": "alice"},
    )
    assert download_response.status_code == 200

    list_response = await async_client.get("/api/jobs/", params={"owner_id": "alice"})
    assert list_response.status_code == 200
    assert any(job["job_id"] == job_id for job in list_response.json())


async def test_sse_event_stream(client: Tuple[httpx.AsyncClient, object], tmp_path: Path) -> None:
    async_client, app = client
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _create_sample_pdf(source_dir / "doc1.pdf")

    create_response = await async_client.post(
        "/api/jobs/",
        json={
            "owner_id": "alice",
            "source_path": str(source_dir),
            "files": [{"filename": "doc1.pdf"}],
        },
    )
    job_id = create_response.json()["job_id"]

    repo = app.state.job_repository  # type: ignore[attr-defined]
    repo.append_event(job_id, level="info", message="hello", metadata={})

    async with async_client.stream(
        "GET", f"/api/jobs/{job_id}/events", params={"owner_id": "alice"}
    ) as response:
        assert response.status_code == 200
        payload_line = None
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                payload_line = line
                break
        assert payload_line is not None
        assert "hello" in payload_line


async def test_owner_mismatch_forbidden(client: Tuple[httpx.AsyncClient, object], tmp_path: Path) -> None:
    async_client, _ = client
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _create_sample_pdf(source_dir / "doc1.pdf")

    job_id = (
        await async_client.post(
            "/api/jobs",
            json={
                "owner_id": "alice",
                "source_path": str(source_dir),
                "files": [{"filename": "doc1.pdf"}],
            },
        )
    ).json()["job_id"]

    forbidden_response = await async_client.get(
        f"/api/jobs/{job_id}", params={"owner_id": "bob"}
    )
    assert forbidden_response.status_code == 403
