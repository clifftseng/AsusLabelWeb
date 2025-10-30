import json
import sys
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis_components import ExtractedDocument, ExtractedPage


def _make_document(text: str = "Battery data") -> ExtractedDocument:
    return ExtractedDocument(
        path=Path("fake.pdf"),
        pages=[ExtractedPage(number=1, text=text)],
    )


def _build_engine(mock_handler):
    from backend.analysis_components import VLLMAnalysisEngine

    transport = httpx.MockTransport(mock_handler)

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url="http://vllm.test", transport=transport)

    return VLLMAnalysisEngine(base_url="http://vllm.test", model="test-model", client_factory=client_factory)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_vllm_engine_extracts_fields_and_combines_hints():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        assert payload["model"] == "test-model"
        assert "Known label values" in payload["prompt"]
        response_text = '{"model_name":"TUF-123","voltage":"15.4V","typ_batt_capacity_wh":"65Wh"}'
        return httpx.Response(200, json={"outputs": [{"text": response_text}]})

    engine = _build_engine(handler)
    document = _make_document("Model Name: something")
    result = await engine.analyse(document, label_hint={"typ_capacity_mah": "4200mAh"})

    assert result["model_name"] == "TUF-123"
    assert result["voltage"] == "15.4V"
    assert result["typ_capacity_mah"] == "4200mAh"


@pytest.mark.anyio
async def test_vllm_engine_raises_on_bad_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"outputs": [{"text": "Not a JSON"}]})

    engine = _build_engine(handler)
    document = _make_document()

    with pytest.raises(RuntimeError):
        await engine.analyse(document)
