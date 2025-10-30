from __future__ import annotations

import asyncio
import json
import os
import re
import logging  # Import the logging module
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import fitz  # type: ignore[import]
from .analysis_components import AnalysisEngine, ExtractedDocument, ExtractedPage, PDFDocumentLoader

os.environ.setdefault('AZURE_HTTP_LOGGING_ENABLED', 'false')
os.environ.setdefault('AZURE_LOG_LEVEL', 'WARNING')

# Get a logger instance for this module
logger = logging.getLogger(__name__)
try:  # Optional Azure dependencies
    from azure.ai.formrecognizer import DocumentAnalysisClient  # type: ignore[import]
    from azure.core.credentials import AzureKeyCredential  # type: ignore[import]
except Exception:  # pragma: no cover - azure libs are optional
    DocumentAnalysisClient = None  # type: ignore[assignment]
    AzureKeyCredential = None  # type: ignore[assignment]

try:  # Optional Azure aio dependency
    from azure.ai.formrecognizer.aio import DocumentAnalysisClient as AioDocumentAnalysisClient  # type: ignore[import]
except Exception:  # pragma: no cover - azure libs are optional
    AioDocumentAnalysisClient = None  # type: ignore[assignment]

try:  # Optional Azure OpenAI dependency
    from openai import AzureOpenAI  # type: ignore[import]
except Exception:  # pragma: no cover - optional at runtime
    AzureOpenAI = None  # type: ignore[assignment]


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


TARGET_FIELDS = [
    "model_name",
    "voltage",
    "typ_batt_capacity_wh",
    "typ_capacity_mah",
    "rated_capacity_mah",
    "rated_energy_wh",
]

MAX_PAGE_TEXT_CHARS = 1800


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


class AzurePagePredictor:
    def __init__(self, *, deployment: Optional[str] = None, max_pages: int = 3) -> None:
        if AzureOpenAI is None:
            raise RuntimeError("openai package is not installed")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        deployment_name = deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not all([endpoint, api_key, deployment_name]):
            raise ValueError("Azure OpenAI environment variables are not fully configured")
        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version="2024-02-01",
        )
        self._deployment = deployment_name
        self._max_pages = max(1, max_pages)

    async def predict(self, pdf_path: Path, *, target_fields: Iterable[str]) -> List[int]:
        try:
            with fitz.open(pdf_path) as doc:
                total_pages = len(doc)
                page_entries: List[Dict[str, object]] = []
                for index, page in enumerate(doc, start=1):
                    text = page.get_text()
                    if not text:
                        continue
                    text = re.sub(r"\s+", " ", text.strip())
                    if not text:
                        continue
                    page_entries.append(
                        {
                            "page": index,
                            "text": text[:MAX_PAGE_TEXT_CHARS],
                        }
                    )
        except Exception as exc:
            logger.exception("Page predictor: failed to read %s: %s", pdf_path.name, exc)
            return []

        if not page_entries:
            return []

        system_prompt = (
            "You identify which pages in a PDF are most likely to contain battery label fields. "
            "The fields of interest are: "
            + ", ".join(target_fields)
            + ". Return a JSON object with an array field named 'pages' listing up to "
            f"{self._max_pages} unique 1-based page numbers in descending order of relevance. "
            "You may optionally include a 'fields' object mapping field names to the pages that likely contain them. "
            "If no informative pages exist, return an empty array."
        )
        user_payload = {
            "total_pages": len(page_entries),
            "pages": page_entries,
            "target_fields": list(target_fields),
        }

        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.chat.completions.create(
                    model=self._deployment,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(user_payload, ensure_ascii=False),
                        },
                    ],
                    max_tokens=512,
                ),
            )
        except Exception as exc:  # pragma: no cover - network failures
            logger.exception("Page predictor: Azure OpenAI request failed: %s", exc)
            return []

        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""

        try:
            payload = json.loads(content) if content else {}
        except json.JSONDecodeError:
            logger.warning("Page predictor: unable to parse Azure OpenAI response as JSON.")
            return []

        candidates: List[int] = []
        if isinstance(payload.get("pages"), list):
            candidates.extend(p for p in payload["pages"] if isinstance(p, int))

        if not candidates and isinstance(payload.get("overall_top_pages"), list):
            candidates.extend(p for p in payload["overall_top_pages"] if isinstance(p, int))

        fields_info = payload.get("fields")
        if isinstance(fields_info, dict):
            field_counter: Counter[int] = Counter()
            for value in fields_info.values():
                if isinstance(value, dict) and isinstance(value.get("pages"), list):
                    field_counter.update(p for p in value["pages"] if isinstance(p, int))
            if field_counter:
                candidates.extend(page for page, _ in field_counter.most_common(self._max_pages))

        cleaned = sorted({p for p in candidates if 1 <= p <= len(page_entries)})[: self._max_pages]
        return cleaned


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
        logger.debug("DI Extractor: Sending image to Azure DI for analysis.")
        poller = self._client.begin_analyze_document(self._model_id, document=image_bytes)
        logger.debug("DI Extractor: Polling Azure DI for result.")
        result = poller.result()
        logger.debug("DI Extractor: Received result from Azure DI.")
        if hasattr(result, "content") and isinstance(result.content, str):
            logger.debug("DI Extractor: Returning content from result.")
            return result.content.strip()
        lines: List[str] = []
        for page in getattr(result, "pages", []):
            for line in getattr(page, "lines", []):
                text = getattr(line, "content", "")
                if text:
                    lines.append(text)
        logger.debug(f"DI Extractor: Returning {len(lines)} lines from result pages.")
        return "\n".join(lines).strip()
    async def extract_full_pages(self, pdf_path: Path, pages: List[int]) -> Dict[int, str]:
        return await asyncio.to_thread(self._extract_full_pages_sync, pdf_path, pages)

    def _extract_full_pages_sync(self, pdf_path: Path, pages: List[int]) -> Dict[int, str]:
        logger.debug(f"DI Extractor: Starting full page extraction for {pdf_path.name}, pages: {pages}")
        results: Dict[int, str] = {}
        unique_pages = sorted(set(page for page in pages if page > 0))
        if not unique_pages:
            logger.debug(f"DI Extractor: No unique pages to process for {pdf_path.name}")
            return results
        with fitz.open(pdf_path) as doc:
            for page_number in unique_pages:
                logger.debug(f"DI Extractor: Processing page {page_number} for {pdf_path.name}")
                index = page_number - 1
                if index < 0 or index >= len(doc):
                    logger.warning(f"DI Extractor: Page {page_number} out of bounds for {pdf_path.name}")
                    continue
                page = doc[index]
                pixmap = page.get_pixmap(dpi=180)
                
                logger.debug(f"DI Extractor: Calling _analyse_bytes for page {page_number} of {pdf_path.name}")
                text = self._analyse_bytes(pixmap.tobytes("png"))
                
                if text:
                    results[page_number] = text
                    logger.debug(f"DI Extractor: Extracted text for page {page_number} of {pdf_path.name}")
                else:
                    logger.debug(f"DI Extractor: No text extracted for page {page_number} of {pdf_path.name}")
        logger.debug(f"DI Extractor: Finished full page extraction for {pdf_path.name}")
        return results


class AzureDocumentIntelligenceExtractorAio:
    def __init__(
        self,
        *,
        endpoint: str,
        key: str,
        model_id: str = "prebuilt-document",
    ) -> None:
        if AioDocumentAnalysisClient is None or AzureKeyCredential is None:
            raise RuntimeError("azure-ai-formrecognizer aio client not available")
        if not endpoint or not key:
            raise ValueError("Document Intelligence endpoint and key must be configured")
        self._endpoint = endpoint
        self._key = key
        self._model_id = model_id
        self._client: Optional[AioDocumentAnalysisClient] = None

    async def _client_async(self) -> AioDocumentAnalysisClient:
        if self._client is None:
            credential = AzureKeyCredential(self._key)
            self._client = AioDocumentAnalysisClient(self._endpoint, credential)
        return self._client

    async def analyse_pdf_pages(self, pdf_path: Path, pages: List[int]) -> Dict[int, str]:
        uniq_pages = sorted({page for page in pages if page > 0})
        if not uniq_pages:
            return {}

        client = await self._client_async()
        pdf_bytes = pdf_path.read_bytes()
        pages_arg = ",".join(str(page) for page in uniq_pages)
        timeout = float(os.getenv("DI_MULTI_TIMEOUT_SEC", "120"))

        logger.debug(
            "DI AIO: begin_analyze_document pages=%s size=%d timeout=%.1fs",
            pages_arg,
            len(pdf_bytes),
            timeout,
        )

        poller = await client.begin_analyze_document(
            self._model_id,
            document=pdf_bytes,
            pages=pages_arg,
        )

        try:
            result = await asyncio.wait_for(poller.result(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("DI AIO: Timeout after %.1fs for pages=%s", timeout, pages_arg)
            try:
                await poller.cancel()
            except Exception:
                pass
            return {}
        except Exception as exc:
            logger.exception("DI AIO: analyse failed for pages=%s: %s", pages_arg, exc)
            return {}

        texts: Dict[int, str] = {}
        try:
            pages_data = getattr(result, "pages", None)
            if pages_data:
                for page in pages_data:
                    page_number = getattr(page, "page_number", None)
                    if not isinstance(page_number, int):
                        continue
                    lines = [
                        getattr(line, "content", "")
                        for line in getattr(page, "lines", [])
                        if isinstance(getattr(line, "content", ""), str) and getattr(line, "content", "").strip()
                    ]
                    combined = "\n".join(lines).strip()
                    if combined:
                        texts[page_number] = combined
            if not texts and isinstance(getattr(result, "content", ""), str):
                combined = result.content.strip()
                if combined:
                    for page_number in uniq_pages:
                        texts[page_number] = combined
        except Exception as exc:
            logger.exception("DI AIO: parse result failed for pages=%s: %s", pages_arg, exc)
        return texts

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


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
        self._aio_document_intelligence_extractor: Optional[AzureDocumentIntelligenceExtractorAio] = None
        self._page_predictor: Optional[AzurePagePredictor] = None

        if all(os.getenv(name) for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")):
            try:
                self._page_predictor = AzurePagePredictor(max_pages=self.max_prediction_pages)
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.warning("Failed to initialise Azure page predictor: %s", exc)

    async def analyse(self, pdf_path: Path) -> tuple[Dict[str, str], List[str]]:
        messages: List[str] = []
        
        # Log the start of analysis for this PDF
        logger.info(f"Starting analysis for PDF: {pdf_path.name}")

        # 檢查是否存在對應的格式範本
        spec = self.format_repository.find_for_pdf(pdf_path) if self.format_repository else None

        if spec and spec.hints:
            # --- Mode A: format-guided extraction ---
            fields = await self._run_mode_a(pdf_path, spec)  # Removed 'log'
            messages.append(f"Mode A: matched format '{spec.name}' and extracted structured fields.")
            # Note: _run_mode_a already logs its internal messages

            return fields, messages

        else:
            # --- Mode B: AI workflow fallback ---
            fields = await self._run_mode_b(pdf_path, log=messages.append)
            messages.append("Mode B: no matching format; completed AI-only analysis workflow.")
            # Note: _run_mode_b already logs its internal messages

            return fields, messages

    async def _run_mode_a(
        self,
        pdf_path: Path,
        spec: FormatSpec,
        # log: Callable[[str], None], # Removed log callback
    ) -> Dict[str, str]:
        logger.info(f"[Mode A] Matched format {spec.name} with {len(spec.hints)} hints for {pdf_path.name}")
        label_hint: Dict[str, str] = {}
        if self.extractor:
            try:
                label_hint = await self.extractor.extract(pdf_path, spec)
                if label_hint:
                    logger.info(f"[Mode A] Format-guided hints produced {len(label_hint)} candidate values for {pdf_path.name}")
            except Exception as exc:  # pragma: no cover - defensive path
                logger.exception(f"[Mode A] Format-guided extraction failed for {pdf_path.name}: {exc}")

        pages = sorted({max(hint.page, 1) for hint in spec.hints})
        page_texts: Dict[int, str] = {}
        if pages and self.document_intelligence_extractor:
            page_texts = await self._gather_page_texts(pdf_path, pages, context="Mode A")
        elif not self.document_intelligence_extractor:
            logger.warning(f"[Mode A] No Document Intelligence extractor configured; falling back to direct text loading for {pdf_path.name}")

        document = await self._build_document_from_page_texts(pdf_path, page_texts, pages or None)
        fields = await self.analysis_engine.analyse(document, label_hint=label_hint or None)
        if fields:
            logger.info(f"[Mode A] Completed AI analysis for {pdf_path.name} with {self._count_non_empty(fields)} populated fields")
        else:
            logger.info(f"[Mode A] AI analysis returned no fields for {pdf_path.name}")
        return fields
    async def _run_mode_b(
        self,
        pdf_path: Path,
        log: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, str]:
        message = f"[Mode B] No matching format for {pdf_path.name}, using AI workflow"
        logger.info(message)
        if log:
            log(message)

        pages_to_process = await self._predict_pages_via_chatgpt(pdf_path, log=log)
        if pages_to_process:
            message = f"[Mode B] Azure OpenAI suggested pages {pages_to_process} for {pdf_path.name}"
            logger.info(message)
            if log:
                log(message)
        else:
            pages_to_process = self._predict_relevant_pages(pdf_path)
            if pages_to_process:
                message = f"[Mode B] Falling back to heuristic pages for {pdf_path.name}: {pages_to_process}"
            else:
                message = (
                    f"[Mode B] Could not determine pages for {pdf_path.name}; "
                    f"using first {self.max_prediction_pages} page(s)"
                )
                pages_to_process = list(range(1, self.max_prediction_pages + 1))
            logger.info(message)
            if log:
                log(message)

        ai_page_texts = await self._gather_page_texts(pdf_path, pages_to_process, log=log)
        document = await self._build_document_from_page_texts(pdf_path, ai_page_texts, pages_to_process)

        fields = await self.analysis_engine.analyse(document)
        logger.info(f"[Mode B] Completed analysis for {pdf_path.name} with {self._count_non_empty(fields)} populated fields")
        return fields
    async def _gather_page_texts(
        self,
        pdf_path: Path,
        pages: List[int],
        log: Optional[Callable[[str], None]] = None,
        context: str = "Mode B",
    ) -> Dict[int, str]:
        if not pages:
            logger.warning(f"[{context}] No pages requested for {pdf_path.name}")
            if log:
                log(f"[{context}] No pages requested for {pdf_path.name}")
            return {}

        use_aio = os.getenv("DI_USE_AIO", "0") == "1"
        texts: Dict[int, str] = {}

        if use_aio:
            aio_extractor = await self._get_aio_document_intelligence_extractor()
            if aio_extractor is None:
                logger.warning(f"[{context}] AIO Document Intelligence unavailable; falling back to sync for {pdf_path.name}")
                if log:
                    log(f"[{context}] AIO Document Intelligence unavailable; falling back to sync for {pdf_path.name}")
            else:
                try:
                    texts = await aio_extractor.analyse_pdf_pages(pdf_path, pages)
                except Exception as exc:  # pragma: no cover - defensive path
                    logger.exception(f"[{context}] AIO Document Intelligence failed for {pdf_path.name}: {exc}")
                    if log:
                        log(f"[{context}] AIO Document Intelligence failed for {pdf_path.name}: {exc}")

        if not texts:
            if self.document_intelligence_extractor is None:
                logger.warning(f"[{context}] Document Intelligence extractor not configured; skipping {pdf_path.name}")
                if log:
                    log(f"[{context}] Document Intelligence extractor not configured; skipping {pdf_path.name}")
                return {}
            try:
                texts = await self.document_intelligence_extractor.extract_full_pages(pdf_path, pages)
            except Exception as exc:  # pragma: no cover - defensive path
                logger.exception(f"[{context}] Document Intelligence failed for {pdf_path.name}: {exc}")
                if log:
                    log(f"[{context}] Document Intelligence failed for {pdf_path.name}: {exc}")
                return {}

        filtered = {page: text for page, text in texts.items() if isinstance(text, str) and text.strip()}
        if filtered:
            message = f"[{context}] Document Intelligence returned text for {len(filtered)} pages for {pdf_path.name}"
            logger.info(message)
            if log:
                log(message)
        else:
            message = f"[{context}] Document Intelligence returned no usable text for {pdf_path.name}"
            logger.info(message)
            if log:
                log(message)
        return filtered

    async def _predict_pages_via_chatgpt(
        self,
        pdf_path: Path,
        log: Optional[Callable[[str], None]] = None,
    ) -> List[int]:
        if self._page_predictor is None:
            return []
        try:
            pages = await self._page_predictor.predict(pdf_path, target_fields=TARGET_FIELDS)
            return pages[: self.max_prediction_pages]
        except Exception as exc:  # pragma: no cover - defensive path
            logger.exception("Azure OpenAI page prediction failed for %s: %s", pdf_path.name, exc)
            if log:
                log(f"[Mode B] Azure OpenAI page prediction failed: {exc}")
            return []

    def _predict_relevant_pages(self, pdf_path: Path) -> List[int]: # Removed log callback
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
            logger.exception(f"[Mode B] Page prediction failed for {pdf_path.name}: {exc}")
            return []

        if not predicted and total_pages:
            limit = min(self.max_prediction_pages, total_pages)
            logger.info(f"[Mode B] No keywords found, defaulting to first {limit} pages for {pdf_path.name}")
            return list(range(1, limit + 1))
        
        logger.info(f"[Mode B] Predicted pages for {pdf_path.name}: {predicted}")
        return predicted
    async def _build_document_from_page_texts(
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
        return await self._load_document(pdf_path, pages_requested)

    async def _load_document(self, pdf_path: Path, pages: Optional[List[int]]) -> ExtractedDocument:
        return await asyncio.to_thread(self._load_document_sync, pdf_path, pages)

    def _load_document_sync(self, pdf_path: Path, pages: Optional[List[int]]) -> ExtractedDocument:
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

    async def aclose(self) -> None:
        if self._aio_document_intelligence_extractor is not None:
            await self._aio_document_intelligence_extractor.close()
            self._aio_document_intelligence_extractor = None

    async def _get_aio_document_intelligence_extractor(self) -> Optional[AzureDocumentIntelligenceExtractorAio]:
        if self._aio_document_intelligence_extractor is not None:
            return self._aio_document_intelligence_extractor

        endpoint = os.getenv("DOCUMENT_INTELLIGENCE_ENDPOINT", "")
        key = os.getenv("DOCUMENT_INTELLIGENCE_KEY", "")
        model_id = os.getenv("DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-document")

        if not endpoint or not key:
            logger.warning("AIO Document Intelligence environment variables are missing; cannot create client.")
            return None

        try:
            self._aio_document_intelligence_extractor = AzureDocumentIntelligenceExtractorAio(
                endpoint=endpoint,
                key=key,
                model_id=model_id,
            )
        except Exception as exc:  # pragma: no cover - defensive path
            logger.exception(f"Failed to initialise AIO Document Intelligence client: {exc}")
            return None

        return self._aio_document_intelligence_extractor
