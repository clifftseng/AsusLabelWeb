import asyncio
from pathlib import Path

import fitz  # type: ignore[import]
import pytest

from analysis_components import HeuristicAnalysisEngine, PDFDocumentLoader
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
        files=[PDFFile(id=1, filename="battery.pdf", is_label=False)],
        label_filename="",
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
async def test_pipeline_reuses_label_fields_when_missing(tmp_path: Path):
    sample_dir = tmp_path / "inputs"
    sample_dir.mkdir()
    label_pdf = sample_dir / "label.pdf"
    product_pdf = sample_dir / "product.pdf"

    _create_pdf(
        label_pdf,
        [
            "Model Name: LABEL-01",
            "Nominal Voltage: 11.1V",
            "Typ Batt Capacity Wh: 50Wh",
            "Typ Capacity mAh: 4400mAh",
            "Rated Capacity mAh: 4200mAh",
            "Rated Energy Wh: 48Wh",
        ],
    )

    _create_pdf(
        product_pdf,
        [
            "Model Name: PROD-77",
            "Nominal Voltage: 11.1V",
            "Typ Batt Capacity Wh: 50Wh",
            "Typ Capacity mAh: 4400mAh",
        ],
    )

    request = AnalyzeRequest(
        path=str(sample_dir),
        files=[
            PDFFile(id=1, filename="label.pdf", is_label=True),
            PDFFile(id=2, filename="product.pdf", is_label=False),
        ],
        label_filename="label.pdf",
    )

    job_state = AnalysisJobState(job_id="job2", request=request, job_dir=tmp_path / "job")
    pipeline = DefaultAnalysisPipeline(
        document_loader=PDFDocumentLoader(max_pages=2),
        analysis_engine=HeuristicAnalysisEngine(),
        sleep_seconds=0.0,
    )
    callbacks = JobCallbacks(job_state)

    await pipeline.run(job_state, callbacks)

    assert len(job_state.results) == 2
    label_result, product_result = job_state.results
    assert label_result.model_name == "LABEL-01"
    assert product_result.model_name == "PROD-77"
    # Missing fields on product should be populated from label inference
    assert product_result.rated_energy_wh == "48Wh"
    assert product_result.rated_capacity_mah == "4200mAh"


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
        files=[PDFFile(id=1, filename="cancel.pdf", is_label=False)],
        label_filename="",
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
