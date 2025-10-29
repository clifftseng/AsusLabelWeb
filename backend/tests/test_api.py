import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Dict, List
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from main import AnalyzeRequest, PDFFile, app, get_analysis_manager


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class StubResult:
    id: int
    filename: str
    model_name: str
    voltage: str
    typ_batt_capacity_wh: str
    typ_capacity_mah: str
    rated_capacity_mah: str
    rated_energy_wh: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "model_name": self.model_name,
            "voltage": self.voltage,
            "typ_batt_capacity_wh": self.typ_batt_capacity_wh,
            "typ_capacity_mah": self.typ_capacity_mah,
            "rated_capacity_mah": self.rated_capacity_mah,
            "rated_energy_wh": self.rated_energy_wh,
        }


@dataclass
class StubJob:
    files: List[Dict[str, Any]]
    label_filename: str
    status: str = "queued"
    progress: float = 0.0
    processed_count: int = 0
    results: List[StubResult] = field(default_factory=list)
    cancelled: bool = False
    total_count: int = 0
    task: asyncio.Task | None = None
    download_ready: bool = False
    download_path: Path | None = None
    error: str | None = None
    current_file: str | None = None

    async def run(self) -> None:
        self.status = "running"
        self.total_count = len(self.files)
        for idx, file_info in enumerate(self.files, start=1):
            if self.cancelled:
                self.status = "cancelled"
                self.current_file = None
                return
            self.current_file = file_info["filename"]
            await asyncio.sleep(0.01)
            result = StubResult(
                id=idx,
                filename=file_info["filename"],
                model_name=f"Model_{idx}",
                voltage=f"{12 + idx}V",
                typ_batt_capacity_wh=f"{50 + idx}Wh",
                typ_capacity_mah=f"{4000 + idx}mAh",
                rated_capacity_mah=f"{3800 + idx}mAh",
                rated_energy_wh=f"{48 + idx}Wh",
            )
            self.results.append(result)
            self.processed_count = idx
            self.progress = round((idx / self.total_count) * 100, 2)
        self.status = "completed"
        self.current_file = None
        self.download_ready = True
        self.download_path = Path(f"/tmp/fake_{uuid4().hex}.xlsx")

    def mark_cancelled(self) -> None:
        self.cancelled = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "progress": self.progress,
            "processed_count": self.processed_count,
            "total_count": self.total_count,
            "results": [r.to_dict() for r in self.results],
            "download_ready": self.download_ready,
            "download_path": str(self.download_path) if self.download_path else None,
            "error": self.error,
            "current_file": self.current_file,
        }


class StubAnalysisManager:
    def __init__(self) -> None:
        self.jobs: Dict[str, StubJob] = {}

    async def start_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        job_id = uuid4().hex
        files, label_filename = self._normalise_payload(payload)
        job = StubJob(files=files, label_filename=label_filename)
        job.task = asyncio.create_task(job.run())
        self.jobs[job_id] = job
        return {"job_id": job_id, "status": job.status}

    def _normalise_payload(self, payload: Dict[str, Any] | AnalyzeRequest) -> tuple[list[Dict[str, Any]], str]:
        if isinstance(payload, AnalyzeRequest):
            files = [self._serialise_pdf_file(item) for item in payload.files]
            return files, payload.label_filename or ""
        files = payload["files"]
        label_filename = payload.get("label_filename", "")
        normalised = [self._serialise_pdf_file(item) for item in files]
        return normalised, label_filename

    @staticmethod
    def _serialise_pdf_file(item: Dict[str, Any] | PDFFile) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        if isinstance(item, PDFFile):
            return item.model_dump()
        if hasattr(item, "model_dump"):
            return item.model_dump()
        return {
            "id": getattr(item, "id", 0),
            "filename": getattr(item, "filename"),
            "is_label": getattr(item, "is_label", False),
        }

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        job = self.jobs[job_id]
        return {"job_id": job_id, **job.to_dict()}

    async def stop_job(self, job_id: str) -> Dict[str, Any]:
        job = self.jobs[job_id]
        job.mark_cancelled()
        try:
            await job.task
        except asyncio.CancelledError:
            pass
        return {"job_id": job_id, **job.to_dict()}

    async def get_download_path(self, job_id: str) -> Path | None:
        job = self.jobs[job_id]
        return job.download_path if job.download_ready else None

    async def shutdown(self) -> None:
        tasks = [job.task for job in self.jobs.values() if job.task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.fixture
def sample_pdf_dir(tmp_path: Path) -> Path:
    (tmp_path / "first.pdf").write_bytes(b"%PDF-1.4 first")
    (tmp_path / "second.pdf").write_bytes(b"%PDF-1.4 second")
    (tmp_path / "notes.txt").write_text("not a pdf")
    return tmp_path


@pytest.fixture
async def stub_manager():
    manager = StubAnalysisManager()
    original_override = app.dependency_overrides.get(get_analysis_manager)
    app.dependency_overrides[get_analysis_manager] = lambda: manager
    yield manager
    if original_override is not None:
        app.dependency_overrides[get_analysis_manager] = original_override
    else:
        app.dependency_overrides.pop(get_analysis_manager, None)
    await manager.shutdown()


@pytest.mark.anyio("asyncio")
async def test_list_pdfs_success(sample_pdf_dir: Path):
    payload = {"path": str(sample_pdf_dir)}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/list-pdfs", json=payload)
    assert response.status_code == 200
    filenames = [item["filename"] for item in response.json()]
    assert filenames == ["first.pdf", "second.pdf"]


@pytest.mark.anyio("asyncio")
async def test_analysis_job_lifecycle(stub_manager: StubAnalysisManager, sample_pdf_dir: Path):
    files_payload = [
        {"id": 1, "filename": "first.pdf", "is_label": False},
        {"id": 2, "filename": "second.pdf", "is_label": False},
    ]
    analyze_payload = {
        "path": str(sample_pdf_dir),
        "files": files_payload,
        "label_filename": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_response = await client.post("/api/analyze/start", json=analyze_payload)
        assert start_response.status_code == 200
        job_id = start_response.json()["job_id"]
        assert job_id in stub_manager.jobs

        final_status: Dict[str, Any] | None = None
        for _ in range(50):
            status_response = await client.get(f"/api/analyze/status/{job_id}")
            assert status_response.status_code == 200
            data = status_response.json()
            if data["status"] == "completed":
                final_status = data
                break
            await asyncio.sleep(0.01)
        assert final_status is not None, "Job did not complete in time"
        assert final_status["progress"] == pytest.approx(100.0)
        assert final_status["processed_count"] == 2
        assert final_status["download_ready"] is True
        assert len(final_status["results"]) == 2


@pytest.mark.anyio("asyncio")
async def test_analysis_job_can_be_cancelled(stub_manager: StubAnalysisManager, sample_pdf_dir: Path):
    files_payload = [
        {"id": 1, "filename": "first.pdf", "is_label": False},
        {"id": 2, "filename": "second.pdf", "is_label": False},
        {"id": 3, "filename": "label.pdf", "is_label": True},
    ]
    analyze_payload = {
        "path": str(sample_pdf_dir),
        "files": files_payload,
        "label_filename": "label.pdf",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_response = await client.post("/api/analyze/start", json=analyze_payload)
        assert start_response.status_code == 200
        job_id = start_response.json()["job_id"]

        cancel_response = await client.post(f"/api/analyze/stop/{job_id}")
        assert cancel_response.status_code == 200
        cancel_data = cancel_response.json()
        assert cancel_data["status"] == "cancelled"
        assert cancel_data["download_ready"] is False
        assert cancel_data["processed_count"] < cancel_data["total_count"]
        assert cancel_data["error"] is None
