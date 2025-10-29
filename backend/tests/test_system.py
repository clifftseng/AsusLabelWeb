import asyncio
from pathlib import Path
from typing import AsyncIterator

import fitz  # type: ignore[import]
import pytest
from httpx import ASGITransport, AsyncClient

from analysis_components import HeuristicAnalysisEngine, PDFDocumentLoader
from main import (
    AnalysisJobState,
    AnalysisManager,
    AnalyzeRequest,
    DefaultAnalysisPipeline,
    JobCallbacks,
    PDFFile,
    app,
    get_analysis_manager,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_pdf(path: Path, lines: list[str]) -> None:
    doc = fitz.open()
    page = doc.new_page()
    cursor_y = 72
    for line in lines:
        page.insert_text((72, cursor_y), line)
        cursor_y += 18
    doc.save(path)
    doc.close()


class ImmediatePipeline(DefaultAnalysisPipeline):
    async def _analyse_document(self, file_path: Path):  # type: ignore[override]
        # Bypass label-service inference but keep heuristic extraction.
        document = self.document_loader.load(file_path)
        fields = await self.analysis_engine.analyse(document)
        return fields, ["已完成啟發式比對"]


@pytest.fixture
async def system_manager(tmp_path: Path) -> AsyncIterator[AnalysisManager]:
    job_dir = tmp_path / "jobs"
    manager = AnalysisManager(
        pipeline_factory=lambda: ImmediatePipeline(
            document_loader=PDFDocumentLoader(max_pages=3),
            analysis_engine=HeuristicAnalysisEngine(),
            sleep_seconds=0.0,
        ),
        base_dir=job_dir,
        max_concurrent_jobs=1,
    )
    original_override = app.dependency_overrides.get(get_analysis_manager)
    app.dependency_overrides[get_analysis_manager] = lambda: manager
    try:
        yield manager
    finally:
        if original_override is not None:
            app.dependency_overrides[get_analysis_manager] = original_override
        else:
            app.dependency_overrides.pop(get_analysis_manager, None)
        tasks = [job.task for job in manager.jobs.values() if job.task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.anyio
async def test_full_api_flow(system_manager: AnalysisManager, tmp_path: Path) -> None:
    sample_root = tmp_path / "inputs"
    sample_root.mkdir()
    pdf_path = sample_root / "battery.pdf"
    _make_pdf(
        pdf_path,
        [
            "Model Name: SYS-001",
            "Nominal Voltage: 11.4V",
            "Typ Batt Capacity Wh: 48Wh",
            "Rated Capacity mAh: 4000mAh",
        ],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        list_response = await client.post(
            "/api/list-pdfs",
            json={"path": str(sample_root)},
        )
        assert list_response.status_code == 200
        payload = list_response.json()
        assert payload == [{"id": 1, "filename": "battery.pdf"}]

        start_response = await client.post(
            "/api/analyze/start",
            json={
                "path": str(sample_root),
                "files": payload,
            },
        )
        assert start_response.status_code == 200
        job_id = start_response.json()["job_id"]
        assert job_id

        # Poll until completed
        for _ in range(20):
            status_response = await client.get(f"/api/analyze/status/{job_id}")
            assert status_response.status_code == 200
            status_body = status_response.json()
            if status_body["status"] == "completed":
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("Job did not complete within expected iterations")

        assert status_body["download_ready"] is True
        assert status_body["results"]
        first_result = status_body["results"][0]
        assert first_result["model_name"] == "SYS-001"
        assert first_result["voltage"] == "11.4V"
        assert any("已完成啟發式比對" in message for message in status_body["messages"])

        download_response = await client.get(f"/api/analyze/download/{job_id}")
        assert download_response.status_code == 200
        assert download_response.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


async def _shutdown_manager(manager: AnalysisManager) -> None:
    tasks = [job.task for job in manager.jobs.values() if job.task]
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.anyio
async def test_stop_job_via_api(tmp_path: Path) -> None:
    sample_root = tmp_path / "inputs"
    sample_root.mkdir()
    pdf_path = sample_root / "battery.pdf"
    _make_pdf(
        pdf_path,
        [
            "Model Name: CANCEL-MODE",
            "Nominal Voltage: 9.9V",
        ],
    )

    class SlowPipeline(ImmediatePipeline):
        async def _analyse_document(self, file_path: Path):  # type: ignore[override]
            await asyncio.sleep(0.2)
            return await super()._analyse_document(file_path)

    manager = AnalysisManager(
        pipeline_factory=lambda: SlowPipeline(
            document_loader=PDFDocumentLoader(max_pages=1),
            analysis_engine=HeuristicAnalysisEngine(),
            sleep_seconds=0.2,
        ),
        base_dir=tmp_path / "jobs_cancel",
        max_concurrent_jobs=1,
    )
    original_override = app.dependency_overrides.get(get_analysis_manager)
    app.dependency_overrides[get_analysis_manager] = lambda: manager

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            start_response = await client.post(
                "/api/analyze/start",
                json={
                    "path": str(sample_root),
                    "files": [{"id": 1, "filename": "battery.pdf"}],
                },
            )
            assert start_response.status_code == 200
            job_id = start_response.json()["job_id"]

            # 等待工作進入 running 狀態
            for _ in range(10):
                status_response = await client.get(f"/api/analyze/status/{job_id}")
                assert status_response.status_code == 200
                status_body = status_response.json()
                if status_body["status"] == "running":
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("工作未進入 running 狀態")

            stop_response = await client.post(f"/api/analyze/stop/{job_id}")
            assert stop_response.status_code == 200
            stop_body = stop_response.json()
            assert stop_body["status"] == "cancelled"
            assert stop_body["processed_count"] <= 1
            assert stop_body["messages"]
    finally:
        if original_override is not None:
            app.dependency_overrides[get_analysis_manager] = original_override
        else:
            app.dependency_overrides.pop(get_analysis_manager, None)
        await _shutdown_manager(manager)
