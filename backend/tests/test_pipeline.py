import asyncio
from pathlib import Path

import fitz  # type: ignore[import]
import pytest

from analysis_components import HeuristicAnalysisEngine, PDFDocumentLoader
from openpyxl import load_workbook
from main import (
    AnalysisJobState,
    AnalyzeRequest,
    DefaultAnalysisPipeline,
    JobCallbacks,
    PDFFile,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _create_pdf(path: Path, lines: list[str]) -> None:
    doc = fitz.open()
    page = doc.new_page()
    cursor_y = 72
    for line in lines:
        page.insert_text((72, cursor_y), line)
        cursor_y += 16
    doc.save(path)
    doc.close()


@pytest.mark.anyio
async def test_default_pipeline_extracts_fields_and_creates_excel(tmp_path: Path):
    sample_dir = tmp_path / "pdfs"
    sample_dir.mkdir()
    pdf_path = sample_dir / "battery.pdf"
    _create_pdf(
        pdf_path,
        [
            "Model Name: XZ-999",
            "Nominal Voltage: 15.4V",
            "Typ Batt Capacity Wh: 65Wh",
            "Typ Capacity mAh: 4200mAh",
            "Rated Capacity mAh: 4000mAh",
            "Rated Energy Wh: 58Wh",
        ],
    )

    request = AnalyzeRequest(
        path=str(sample_dir),
        files=[PDFFile(id=1, filename="battery.pdf")],
    )
    job_state = AnalysisJobState(job_id="job1", request=request, job_dir=tmp_path / "job")
    pipeline = DefaultAnalysisPipeline(
        document_loader=PDFDocumentLoader(max_pages=2),
        analysis_engine=HeuristicAnalysisEngine(),
        sleep_seconds=0.0,
    )
    callbacks = JobCallbacks(job_state)

    result = await pipeline.run(job_state, callbacks)

    assert result.download_path is not None
    assert result.download_path.exists()
    assert job_state.processed_count == 1
    assert job_state.progress == pytest.approx(100.0)
    assert job_state.results[0].model_name == "XZ-999"
    assert job_state.results[0].voltage == "15.4V"


@pytest.mark.anyio
async def test_pipeline_handles_missing_fields_without_label(tmp_path: Path):
    sample_dir = tmp_path / "inputs"
    sample_dir.mkdir()
    pdf_path = sample_dir / "product.pdf"

    _create_pdf(
        pdf_path,
        [
            "Model Name: PROD-77",
            "Nominal Voltage: 11.1V",
        ],
    )

    request = AnalyzeRequest(
        path=str(sample_dir),
        files=[PDFFile(id=1, filename="product.pdf")],
    )

    job_state = AnalysisJobState(job_id="job2", request=request, job_dir=tmp_path / "job")
    pipeline = DefaultAnalysisPipeline(
        document_loader=PDFDocumentLoader(max_pages=2),
        analysis_engine=HeuristicAnalysisEngine(),
        sleep_seconds=0.0,
    )
    callbacks = JobCallbacks(job_state)

    await pipeline.run(job_state, callbacks)

    assert len(job_state.results) == 1
    result = job_state.results[0]
    assert result.model_name == "PROD-77"
    # 保持缺漏欄位為空字串，避免產生隨機值
    assert result.typ_batt_capacity_wh == ""
    assert result.typ_capacity_mah == ""

    excel_path = job_state.job_dir / "analysis_result.xlsx"
    workbook = load_workbook(excel_path)
    worksheet = workbook.active
    assert worksheet["E2"].fill.start_color.rgb == "FFFDEB95"


@pytest.mark.anyio
async def test_pipeline_honours_cancellation(tmp_path: Path):
    sample_dir = tmp_path / "pdfs"
    sample_dir.mkdir()
    pdf_path = sample_dir / "cancel.pdf"
    _create_pdf(
        pdf_path,
        [
            "Model Name: CANCEL",
            "Nominal Voltage: 10V",
        ],
    )

    request = AnalyzeRequest(
        path=str(sample_dir),
        files=[PDFFile(id=1, filename="cancel.pdf")],
    )
    job_state = AnalysisJobState(job_id="job3", request=request, job_dir=tmp_path / "job")
    job_state.cancel_event.set()

    pipeline = DefaultAnalysisPipeline(
        document_loader=PDFDocumentLoader(max_pages=1),
        analysis_engine=HeuristicAnalysisEngine(),
        sleep_seconds=0.0,
    )
    callbacks = JobCallbacks(job_state)

    result = await pipeline.run(job_state, callbacks)

    assert result.download_path is None
    assert job_state.results == []
    assert job_state.progress == pytest.approx(0.0)


@pytest.mark.anyio
async def test_pipeline_respects_label_analysis_service(tmp_path: Path):
    sample_dir = tmp_path / "pdfs"
    sample_dir.mkdir()
    pdf_path = sample_dir / "battery.pdf"
    _create_pdf(pdf_path, ["dummy"])

    request = AnalyzeRequest(
        path=str(sample_dir),
        files=[PDFFile(id=1, filename="battery.pdf")],
    )
    job_state = AnalysisJobState(job_id="job4", request=request, job_dir=tmp_path / "job")

    class StubLabelService:
        def __init__(self) -> None:
            self.calls: list[Path] = []

        async def analyse(self, pdf: Path):
            self.calls.append(pdf)
            return (
                {
                    "model_name": "from_stub",
                    "voltage": "42V",
                },
                ["stub message"],
            )

    stub_service = StubLabelService()
    pipeline = DefaultAnalysisPipeline(
        document_loader=PDFDocumentLoader(max_pages=1),
        analysis_engine=HeuristicAnalysisEngine(),
        sleep_seconds=0.0,
        label_service=stub_service,  # type: ignore[arg-type]
    )
    callbacks = JobCallbacks(job_state)

    await pipeline.run(job_state, callbacks)

    assert stub_service.calls == [pdf_path]
    assert job_state.results[0].model_name == "from_stub"
    assert any("stub message" in message for message in job_state.messages)
