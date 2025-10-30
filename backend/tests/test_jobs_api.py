from __future__ import annotations

from pathlib import Path
import sys

import httpx
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.main import create_app
from backend.settings import AppSettings


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    (src / "doc1.pdf").write_text("pdf", encoding="utf-8")
    (src / "doc2.pdf").write_text("pdf", encoding="utf-8")
    return src


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    settings = AppSettings(
        job_queue_url=f"sqlite:///{tmp_path/'queue.db'}",
        job_storage_root=tmp_path / "jobs",
        job_max_workers=0,
    )
    app = create_app(settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True) as async_client:
        yield async_client, app


@pytest.mark.asyncio
async def test_create_job_endpoint(client, source_dir: Path) -> None:
    async_client, _ = client
    response = await async_client.post(
        "/api/jobs/",
        json={
            "owner_id": "alice",
            "source_path": str(source_dir),
            "files": [{"filename": "doc1.pdf"}],
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "queued"
    job_id = data["job_id"]

    detail = (await async_client.get(f"/api/jobs/{job_id}", params={"owner_id": "alice"})).json()
    assert detail["job_id"] == job_id
    assert detail["total_files"] == 1
    assert detail["events"][-1]["message"].lower().startswith("job queued")


@pytest.mark.asyncio
async def test_cancel_job_endpoint(client, source_dir: Path) -> None:
    async_client, _ = client
    job_response = await async_client.post(
        "/api/jobs/",
        json={
            "owner_id": "alice",
            "source_path": str(source_dir),
            "files": [{"filename": "doc1.pdf"}],
        },
    )
    job_id = job_response.json()["job_id"]

    response = await async_client.post(
        f"/api/jobs/{job_id}/cancel", params={"owner_id": "alice"}, json={"reason": "user"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    detail = (await async_client.get(f"/api/jobs/{job_id}", params={"owner_id": "alice"})).json()
    assert detail["status"] == "cancelled"
    assert detail["events"][-1]["level"] == "warning"


@pytest.mark.asyncio
async def test_get_job_forbidden_for_different_owner(client, source_dir: Path) -> None:
    async_client, _ = client
    job_response = await async_client.post(
        "/api/jobs/",
        json={
            "owner_id": "alice",
            "source_path": str(source_dir),
            "files": [{"filename": "doc1.pdf"}],
        },
    )
    job_id = job_response.json()["job_id"]

    response = await async_client.get(f"/api/jobs/{job_id}", params={"owner_id": "bob"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_download_endpoint_returns_file(client, source_dir: Path) -> None:
    async_client, app = client
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
    service = app.state.job_service  # type: ignore[attr-defined]
    claimed = repo.acquire_next_job(worker_id="tester")
    assert claimed and claimed.job_id == job_id

    job_dir = service.job_directory(job_id)
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "result.xlsx"
    report_path.write_text("dummy", encoding="utf-8")

    repo.complete_job(
        job_id=job_id,
        worker_id="tester",
        output_manifest=[{"filename": "doc1.pdf", "status": "ok"}],
        download_path=str(report_path),
    )

    response = await async_client.get(
        f"/api/jobs/{job_id}/download", params={"owner_id": "alice"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument",
    )
    assert response.content
