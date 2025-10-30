import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import fitz  # type: ignore[import]
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis_components import HeuristicAnalysisEngine, PDFDocumentLoader
from backend.document_analysis import (
    AzureDocumentIntelligenceExtractor,
    FormatGuidedExtractor,
    FormatRepository,
    FormatSpec,
    LabelAnalysisService,
)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def _create_pdf(path: Path, lines: list[str]) -> None:
    doc = fitz.open()
    page = doc.new_page()
    cursor_y = 72
    for line in lines:
        page.insert_text((72, cursor_y), line)
        cursor_y += 40
    doc.save(path)
    doc.close()


def test_format_repository_prefers_longest_match(tmp_path: Path):
    format_dir = tmp_path / 'format'
    format_dir.mkdir()
    (format_dir / 'foo.json').write_text('{"hints": []}')
    (format_dir / 'foo_bar.json').write_text('{"hints": []}')

    repository = FormatRepository(format_dir)

    spec = repository.find_for_pdf(Path('foo_bar_v1.pdf'))

    assert spec is not None
    assert spec.name == 'foo_bar'


@pytest.mark.anyio
async def test_format_guided_extractor_reads_bbox(tmp_path: Path):
    pdf_path = tmp_path / 'sample.pdf'
    _create_pdf(
        pdf_path,
        [
            'Model Name: TEST-01',
            'Nominal Voltage: 15.2V',
            'Rated Energy Wh: 55Wh',
        ],
    )

    spec = FormatSpec(
        name='sample',
        hints=[
            {'field': 'model_name', 'page': 1, 'bbox': [60, 60, 500, 30]},
            {'field': 'voltage', 'page': 1, 'bbox': [60, 100, 500, 30]},
        ],
    )

    extractor = FormatGuidedExtractor()

    fields = await extractor.extract(pdf_path, spec)

    assert 'Model Name: TEST-01' in fields['model_name']
    assert '15.2' in fields['voltage']


@pytest.mark.anyio
async def test_label_analysis_service_mode_a_uses_format_only(tmp_path: Path):
    pdf_path = tmp_path / 'battery.pdf'
    _create_pdf(
        pdf_path,
        [
            'Model Name: FMT-123',
            'Nominal Voltage: 12.5V',
            'Typ Batt Capacity Wh: 40Wh',
        ],
    )

    format_dir = tmp_path / 'format'
    format_dir.mkdir()
    (format_dir / 'battery.json').write_text(
        json.dumps(
                {
                    'hints': [
                        {'field': 'model_name', 'page': 1, 'bbox': [60, 60, 300, 30]},
                    ]
                }
            )
        )

    repository = FormatRepository(format_dir)
    extractor = FormatGuidedExtractor()
    service = LabelAnalysisService(
        document_loader=PDFDocumentLoader(max_pages=1),
        analysis_engine=HeuristicAnalysisEngine(),
        format_repository=repository,
        extractor=extractor,
    )

    fields, messages = await service.analyse(pdf_path)

    assert 'model_name' in fields
    assert 'FMT-123' in fields['model_name']
    assert any('Mode A' in message for message in messages)


@pytest.mark.anyio
async def test_document_intelligence_extractor_uses_client(tmp_path: Path):
    pdf_path = tmp_path / 'di.pdf'
    _create_pdf(
        pdf_path,
        [
            'Model Name: DI-001',
            'Voltage: 9.9V',
        ],
    )

    spec = FormatSpec(
        name='di',
        hints=[{'field': 'model_name', 'page': 1, 'bbox': [60, 60, 400, 30]}],
    )

    class StubPoller:
        def __init__(self, text: str) -> None:
            self._text = text

        def result(self):
            class Result:
                def __init__(self, text: str) -> None:
                    self.content = text
            return Result(self._text)

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[bytes] = []

        def begin_analyze_document(self, model_id: str, *, document: bytes):
            self.calls.append(document)
            return StubPoller("Model Name: DI-001")

    stub_client = StubClient()
    extractor = AzureDocumentIntelligenceExtractor(client=stub_client)

    fields = await extractor.extract(pdf_path, spec)

    assert fields['model_name'] == 'Model Name: DI-001'
    assert len(stub_client.calls) == 1


@pytest.mark.anyio
async def test_label_analysis_service_mode_b_uses_document_intelligence(tmp_path: Path):
    pdf_path = tmp_path / 'battery.pdf'
    _create_pdf(
        pdf_path,
        [
            'Nominal Voltage: 10.8V',
        ],
    )

    class StubDIExtractor:
        async def extract_full_pages(self, _pdf_path: Path, pages: list[int]) -> Dict[int, str]:
            assert pages == [1]
            return {1: 'Nominal Voltage: 10.8V'}

    service = LabelAnalysisService(
        document_loader=PDFDocumentLoader(max_pages=1),
        analysis_engine=HeuristicAnalysisEngine(),
        format_repository=None,
        document_intelligence_extractor=StubDIExtractor(),  # type: ignore[arg-type]
        max_prediction_pages=1,
    )

    fields, messages = await service.analyse(pdf_path)

    assert fields['voltage'] == '10.8V'
    assert any('Mode B' in message for message in messages)
    assert any('Document Intelligence returned text' in message for message in messages)
