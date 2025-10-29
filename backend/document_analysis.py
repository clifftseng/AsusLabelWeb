from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import fitz  # type: ignore[import]
from analysis_components import AnalysisEngine, ExtractedDocument, ExtractedPage, PDFDocumentLoader

try:  # Optional Azure dependencies
    from azure.ai.formrecognizer import DocumentAnalysisClient  # type: ignore[import]
    from azure.core.credentials import AzureKeyCredential  # type: ignore[import]
except Exception:  # pragma: no cover - azure libs are optional
    DocumentAnalysisClient = None  # type: ignore[assignment]
    AzureKeyCredential = None  # type: ignore[assignment]


@dataclass
class FormatHint:
    field: str
    page: int
    bbox: List[float]

    def as_rect(self) -> fitz.Rect:
        if len(self.bbox) != 4:
            raise ValueError("bbox must contain four numeric values")
        x, y, width, height = self.bbox
        return fitz.Rect(x, y, x + width, y + height)


@dataclass
class FormatSpec:
    name: str
    hints: List[FormatHint] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalised: List[FormatHint] = []
        for hint in self.hints:
            if isinstance(hint, FormatHint):
                normalised.append(hint)
            elif isinstance(hint, dict):
                field = hint.get("field")
                page = hint.get("page", 1)
                bbox = hint.get("bbox")
                if isinstance(field, str) and isinstance(page, int) and isinstance(bbox, list):
                    normalised.append(FormatHint(field=field, page=page, bbox=bbox))
        self.hints = normalised

    @classmethod
    def from_dict(cls, name: str, payload: Dict[str, object]) -> "FormatSpec":
        raw_hints = payload.get("hints", [])
        hints: List[FormatHint] = []
        if isinstance(raw_hints, list):
            for entry in raw_hints:
                if not isinstance(entry, dict):
                    continue
                field = entry.get("field")
                page = entry.get("page", 1)
                bbox = entry.get("bbox")
                if not isinstance(field, str) or not isinstance(page, int) or not isinstance(bbox, list):
                    continue
                hints.append(FormatHint(field=field, page=page, bbox=bbox))
        return cls(name=name, hints=hints)


class FormatRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._cache: Dict[str, FormatSpec] = {}

    def _load_spec(self, name: str) -> Optional[FormatSpec]:
        if name in self._cache:
            return self._cache[name]
        path = self.directory / f"{name}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        spec = FormatSpec.from_dict(name, data)
        self._cache[name] = spec
        return spec

    def all_spec_names(self) -> Iterable[str]:
        for file in self.directory.glob("*.json"):
            yield file.stem

    def find_for_pdf(self, pdf_path: Path) -> Optional[FormatSpec]:
        stem = pdf_path.stem.lower()
        best_spec: Optional[FormatSpec] = None
        for name in self.all_spec_names():
            if name.lower() in stem:
                spec = self._load_spec(name)
                if spec is None:
                    continue
                if best_spec is None or len(spec.name) > len(best_spec.name):
                    best_spec = spec
        return best_spec


class FormatGuidedExtractor:
    async def extract(self, pdf_path: Path, spec: FormatSpec) -> Dict[str, str]:
        return await asyncio.to_thread(self._extract_sync, pdf_path, spec)

    def _extract_sync(self, pdf_path: Path, spec: FormatSpec) -> Dict[str, str]:
        results: Dict[str, str] = {}
        with fitz.open(pdf_path) as doc:
            for hint in spec.hints:
                page_index = max(hint.page - 1, 0)
                if page_index >= len(doc):
                    continue
                rect = hint.as_rect()
                page = doc[page_index]
                words = page.get_text("words")
                selected: List[tuple[float, float, str]] = []
                for x0, y0, x1, y1, word, *_ in words:
                    word_rect = fitz.Rect(x0, y0, x1, y1)
                    if not rect.intersects(word_rect):
                        continue
                    center_y = (y0 + y1) / 2
                    if not (rect.y0 <= center_y <= rect.y1):
                        continue
                    selected.append((x0, y0, word))
                if not selected:
                    continue
                selected.sort(key=lambda item: (round(item[1], 1), item[0]))
                text = " ".join(word for _, _, word in selected if word)
                if text:
                    results[hint.field] = text
        return results


class AzureDocumentIntelligenceExtractor:
    def __init__(
        self,
        *,
        client: Optional[DocumentAnalysisClient] = None,
        model_id: str = "prebuilt-document",
    ) -> None:
        if client is None:
            if DocumentAnalysisClient is None or AzureKeyCredential is None:
                raise RuntimeError("azure-ai-formrecognizer 套件未安裝")
            endpoint = os.getenv("DOCUMENT_INTELLIGENCE_ENDPOINT")
            key = os.getenv("DOCUMENT_INTELLIGENCE_KEY")
            if not endpoint or not key:
                raise ValueError("Document Intelligence 環境變數未設定")
            credential = AzureKeyCredential(key)
            client = DocumentAnalysisClient(endpoint, credential)
        self._client = client
        self._model_id = model_id

    async def extract(self, pdf_path: Path, spec: FormatSpec) -> Dict[str, str]:
        return await asyncio.to_thread(self._extract_sync, pdf_path, spec)

    def _extract_sync(self, pdf_path: Path, spec: FormatSpec) -> Dict[str, str]:
        results: Dict[str, str] = {}
        with fitz.open(pdf_path) as doc:
            for hint in spec.hints:
                page_index = max(hint.page - 1, 0)
                if page_index >= len(doc):
                    continue
                rect = hint.as_rect()
                if rect.width <= 0 or rect.height <= 0:
                    continue
                page = doc[page_index]
                pixmap = page.get_pixmap(clip=rect, dpi=180)
                image_bytes = pixmap.tobytes("png")
                text = self._analyse_bytes(image_bytes)
                if text:
                    results.setdefault(hint.field, text)
        return results

    def _analyse_bytes(self, image_bytes: bytes) -> str:
        poller = self._client.begin_analyze_document(self._model_id, document=image_bytes)
        result = poller.result()
        if hasattr(result, "content") and isinstance(result.content, str):
            return result.content.strip()
        lines: List[str] = []
        for page in getattr(result, "pages", []):
            for line in getattr(page, "lines", []):
                text = getattr(line, "content", "")
                if text:
                    lines.append(text)
        return "\n".join(lines).strip()

    async def extract_full_pages(self, pdf_path: Path, pages: List[int]) -> Dict[int, str]:
        return await asyncio.to_thread(self._extract_full_pages_sync, pdf_path, pages)

    def _extract_full_pages_sync(self, pdf_path: Path, pages: List[int]) -> Dict[int, str]:
        results: Dict[int, str] = {}
        unique_pages = sorted(set(page for page in pages if page > 0))
        if not unique_pages:
            return results
        with fitz.open(pdf_path) as doc:
            for page_number in unique_pages:
                index = page_number - 1
                if index < 0 or index >= len(doc):
                    continue
                page = doc[index]
                pixmap = page.get_pixmap(dpi=180)
                text = self._analyse_bytes(pixmap.tobytes("png"))
                if text:
                    results[page_number] = text
        return results


class LabelAnalysisService:
    _MODE_B_KEYWORDS = [
        "model",
        "voltage",
        "capacity",
        "energy",
        "mah",
        "wh",
        "battery",
    ]

    def __init__(
        self,
        *,
        document_loader: PDFDocumentLoader,
        analysis_engine: AnalysisEngine,
        format_repository: Optional[FormatRepository] = None,
        extractor: Optional[FormatGuidedExtractor] = None,
        document_intelligence_extractor: Optional[AzureDocumentIntelligenceExtractor] = None,
        max_prediction_pages: int = 3,
    ) -> None:
        self.document_loader = document_loader
        self.analysis_engine = analysis_engine
        self.format_repository = format_repository
        self.extractor = extractor or FormatGuidedExtractor()
        self.document_intelligence_extractor = document_intelligence_extractor
        self.max_prediction_pages = max(1, max_prediction_pages)

    async def analyse(self, pdf_path: Path) -> tuple[Dict[str, str], List[str]]:
        messages: List[str] = []
        log = messages.append

        spec = self.format_repository.find_for_pdf(pdf_path) if self.format_repository else None
        if spec and spec.hints:
            fields = await self._run_mode_a(pdf_path, spec, log)
        else:
            fields = await self._run_mode_b(pdf_path, log)
        return fields, messages

    async def _run_mode_a(
        self,
        pdf_path: Path,
        spec: FormatSpec,
        log: Callable[[str], None],
    ) -> Dict[str, str]:
        log(f"[Mode A] Matched format {spec.name} with {len(spec.hints)} hints")
        if not self.extractor:
            log("[Mode A] No extractor configured for format workflow")
            return {}
        try:
            fields = await self.extractor.extract(pdf_path, spec)
        except Exception as exc:  # pragma: no cover - defensive path
            log(f"[Mode A] Format-guided extraction failed: {exc}")
            return {}

        if fields:
            log(f"[Mode A] Extracted {len(fields)} fields via format hints")
        else:
            log("[Mode A] Format extraction returned no fields")
        return fields

    async def _run_mode_b(
        self,
        pdf_path: Path,
        log: Callable[[str], None],
    ) -> Dict[str, str]:
        log("[Mode B] No matching format, using AI workflow")
        pages_to_process = self._predict_relevant_pages(pdf_path, log)
        if pages_to_process:
            log(f"[Mode B] Predicted pages: {pages_to_process}")
        else:
            log("[Mode B] Could not predict pages, using default range")
            pages_to_process = None

        ai_page_texts = await self._gather_page_texts(pdf_path, pages_to_process or [], log)
        document = self._build_document_for_mode_b(pdf_path, ai_page_texts, pages_to_process)

        fields = await self.analysis_engine.analyse(document)
        log(f"[Mode B] Completed analysis with {self._count_non_empty(fields)} populated fields")
        return fields

    async def _gather_page_texts(
        self,
        pdf_path: Path,
        pages: List[int],
        log: Callable[[str], None],
    ) -> Dict[int, str]:
        if not pages or not self.document_intelligence_extractor:
            return {}
        try:
            texts = await self.document_intelligence_extractor.extract_full_pages(pdf_path, pages)
        except Exception as exc:  # pragma: no cover - defensive path
            log(f"[Mode B] Document Intelligence failed: {exc}")
            return {}

        filtered = {page: text for page, text in texts.items() if isinstance(text, str) and text.strip()}
        if filtered:
            log(f"[Mode B] Document Intelligence returned text for {len(filtered)} pages")
        else:
            log("[Mode B] Document Intelligence returned no usable text")
        return filtered

    def _predict_relevant_pages(self, pdf_path: Path, log: Callable[[str], None]) -> List[int]:
        predicted: List[int] = []
        total_pages = 0
        try:
            with fitz.open(pdf_path) as doc:
                total_pages = len(doc)
                for index, page in enumerate(doc, start=1):
                    text = page.get_text().lower()
                    if any(keyword in text for keyword in self._MODE_B_KEYWORDS):
                        predicted.append(index)
                    if len(predicted) >= self.max_prediction_pages:
                        break
        except Exception as exc:  # pragma: no cover - defensive path
            log(f"[Mode B] Page prediction failed: {exc}")
            return []

        if not predicted and total_pages:
            limit = min(self.max_prediction_pages, total_pages)
            return list(range(1, limit + 1))
        return predicted

    def _build_document_for_mode_b(
        self,
        pdf_path: Path,
        page_texts: Dict[int, str],
        pages_requested: Optional[List[int]],
    ) -> ExtractedDocument:
        if page_texts:
            pages = [
                ExtractedPage(number=page, text=text)
                for page, text in sorted(page_texts.items())
            ]
            return ExtractedDocument(path=pdf_path, pages=pages)
        return self._load_document(pdf_path, pages_requested)

    def _load_document(self, pdf_path: Path, pages: Optional[List[int]]) -> ExtractedDocument:
        if not pages:
            return self.document_loader.load(pdf_path)

        unique_pages = sorted(set(page for page in pages if page > 0))
        extracted_pages: List[ExtractedPage] = []
        with fitz.open(pdf_path) as doc:
            for page_number in unique_pages:
                index = page_number - 1
                if index < 0 or index >= len(doc):
                    continue
                text = doc[index].get_text()
                extracted_pages.append(ExtractedPage(number=page_number, text=text))

        if not extracted_pages:
            return self.document_loader.load(pdf_path)
        return ExtractedDocument(path=pdf_path, pages=extracted_pages)

    @staticmethod
    def _count_non_empty(payload: Dict[str, str]) -> int:
        return sum(1 for value in payload.values() if isinstance(value, str) and value.strip())
