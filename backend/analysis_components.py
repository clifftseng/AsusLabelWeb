from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

import fitz  # type: ignore[import]
import httpx

try:  # Optional dependency for Azure OpenAI
    from openai import AzureOpenAI  # type: ignore[import]
except Exception:  # pragma: no cover - library may be absent during local tests
    AzureOpenAI = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ExtractedPage:
    number: int
    text: str


@dataclass
class ExtractedDocument:
    path: Path
    pages: List[ExtractedPage]

    @property
    def combined_text(self) -> str:
        return "\n".join(page.text for page in self.pages)


class DocumentLoader(Protocol):
    def load(self, path: Path) -> ExtractedDocument: ...


class AnalysisEngine(Protocol):
    async def analyse(
        self,
        document: ExtractedDocument,
        *,
        label_hint: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        ...


class PDFDocumentLoader:
    """Reads PDF content via PyMuPDF (fitz) and returns the first N pages."""

    def __init__(self, max_pages: int = 5) -> None:
        self.max_pages = max_pages

    def load(self, path: Path) -> ExtractedDocument:
        if not path.exists():
            raise FileNotFoundError(f"找不到指定的 PDF 檔案: {path}")
        pages: List[ExtractedPage] = []
        with fitz.open(path) as doc:
            for index, page in enumerate(doc, start=1):
                text = page.get_text()
                pages.append(ExtractedPage(number=index, text=text))
                if 0 < self.max_pages <= index:
                    break
        return ExtractedDocument(path=path, pages=pages)


class HeuristicAnalysisEngine:
    """Lightweight, deterministic field extractor based on simple heuristics."""

    FIELD_ALIASES: Dict[str, str] = {
        "model name": "model_name",
        "model": "model_name",
        "nominal voltage": "voltage",
        "voltage": "voltage",
        "typ batt capacity wh": "typ_batt_capacity_wh",
        "typical batt capacity wh": "typ_batt_capacity_wh",
        "typ capacity mah": "typ_capacity_mah",
        "typical capacity mah": "typ_capacity_mah",
        "rated capacity mah": "rated_capacity_mah",
        "rated energy wh": "rated_energy_wh",
        "rated energy": "rated_energy_wh",
    }

    NUMERIC_PATTERNS: Dict[str, List[re.Pattern[str]]] = {
        "voltage": [
            re.compile(r"(?:nominal|rated)?\s*voltage[^0-9]*?(\d+(?:\.\d+)?)\s*(?:v|volt)", re.IGNORECASE),
            re.compile(r"(\d+(?:\.\d+)?)\s*(?:v|volt)", re.IGNORECASE),
        ],
        "typ_batt_capacity_wh": [
            re.compile(r"typ(?:ical)?\s+batt(?:ery)?\s+capacity[^0-9]*?(\d+(?:\.\d+)?)\s*wh", re.IGNORECASE),
        ],
        "typ_capacity_mah": [
            re.compile(r"typ(?:ical)?\s+capacity[^0-9]*?(\d+(?:\.\d+)?)\s*mAh", re.IGNORECASE),
        ],
        "rated_capacity_mah": [
            re.compile(r"rated\s+capacity[^0-9]*?(\d+(?:\.\d+)?)\s*mAh", re.IGNORECASE),
        ],
        "rated_energy_wh": [
            re.compile(r"rated\s+energy[^0-9]*?(\d+(?:\.\d+)?)\s*Wh", re.IGNORECASE),
        ],
    }

    def __init__(self, include_numeric_patterns: bool = True) -> None:
        self.include_numeric_patterns = include_numeric_patterns

    async def analyse(
        self,
        document: ExtractedDocument,
        *,
        label_hint: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        lines = self._normalise_lines(document)
        extracted: Dict[str, str] = {}

        # First pass: explicit key/value pairs
        for key, value in lines:
            normalised_key = self.FIELD_ALIASES.get(key)
            if normalised_key and normalised_key not in extracted:
                extracted[normalised_key] = value

        # Second pass: numeric patterns over the entire text
        if self.include_numeric_patterns:
            text = document.combined_text
            for field, patterns in self.NUMERIC_PATTERNS.items():
                if field in extracted:
                    continue
                for pattern in patterns:
                    match = pattern.search(text)
                    if match:
                        extracted[field] = self._format_numeric(match.group(1), field)
                        break

        # Fill with label hints when available
        if label_hint:
            for key, value in label_hint.items():
                extracted.setdefault(key, value)

        return extracted

    def _normalise_lines(self, document: ExtractedDocument) -> List[tuple[str, str]]:
        lines: List[tuple[str, str]] = []
        for page in document.pages:
            for raw_line in page.text.splitlines():
                clean_line = re.sub(r"\s+", " ", raw_line.strip())
                if not clean_line:
                    continue
                split = re.split(r"[:：]", clean_line, maxsplit=1)
                if len(split) != 2:
                    continue
                key, value = split[0].strip().lower(), split[1].strip()
                if key and value:
                    lines.append((key, value))
        return lines

    def _format_numeric(self, value: str, field: str) -> str:
        unit = ""
        if field.endswith("_wh"):
            unit = "Wh"
        elif field.endswith("_mah"):
            unit = "mAh"
        elif field == "voltage":
            unit = "V"

        try:
            numeric = float(value)
            if numeric.is_integer():
                numeric = int(numeric)
            formatted = str(numeric)
        except ValueError:  # pragma: no cover - fallback for unexpected values
            formatted = value

        return f"{formatted}{unit}" if unit and unit.lower() not in value.lower() else formatted


class AzureChatAnalysisEngine:
    """Wraps Azure OpenAI Chat Completions to extract structured information."""

    SYSTEM_PROMPT = (
        "You are an assistant that extracts battery label information from PDF text. "
        "Always return a JSON object with the keys: "
        "model_name, voltage, typ_batt_capacity_wh, typ_capacity_mah, "
        "rated_capacity_mah, rated_energy_wh. "
        "Keep units in the output when available."
    )

    def __init__(self, deployment: Optional[str] = None) -> None:
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

    async def analyse(
        self,
        document: ExtractedDocument,
        *,
        label_hint: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        user_prompt = self._build_prompt(document, label_hint)
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.chat.completions.create(
                model=self._deployment,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            ),
        )
        content = response.choices[0].message.content if response.choices else "{}"
        try:
            import json

            payload = json.loads(content or "{}")
        except json.JSONDecodeError:  # pragma: no cover - defensive
            payload = {}
        return {key: str(value) for key, value in payload.items() if isinstance(value, (str, int, float))}

    def _build_prompt(self, document: ExtractedDocument, label_hint: Optional[Dict[str, str]]) -> str:
        hint_text = ""
        if label_hint:
            hint_text = "Known label values:\n" + "\n".join(f"- {k}: {v}" for k, v in label_hint.items())
        return (
            "Extract the required fields from the following PDF text content.\n"
            f"{hint_text}\n\n"
            f"Text:\n{document.combined_text}\n"
        )


class VLLMAnalysisEngine:
    """Calls a vLLM inference endpoint and expects JSON structured output."""

    REQUIRED_FIELDS = [
        "model_name",
        "voltage",
        "typ_batt_capacity_wh",
        "typ_capacity_mah",
        "rated_capacity_mah",
        "rated_energy_wh",
    ]

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        model: Optional[str] = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 30.0,
        client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
    ) -> None:
        self.base_url = base_url or os.getenv("VLLM_BASE_URL")
        if not self.base_url:
            raise ValueError("VLLM_BASE_URL is not configured")
        self.model = model or os.getenv("VLLM_MODEL")
        if not self.model:
            raise ValueError("VLLM_MODEL is not configured")
        self.temperature = temperature if temperature is not None else float(os.getenv("VLLM_TEMPERATURE", "0.0"))
        self.max_tokens = max_tokens if max_tokens is not None else int(os.getenv("VLLM_MAX_TOKENS", "512"))
        self.timeout = timeout
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout))

    async def analyse(
        self,
        document: ExtractedDocument,
        *,
        label_hint: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        prompt = self._build_prompt(document, label_hint)
        response_text = await self._call_vllm(prompt)
        payload = self._parse_payload(response_text)
        fields: Dict[str, str] = {}
        for key in self.REQUIRED_FIELDS:
            value = payload.get(key)
            if value in (None, ""):
                continue
            fields[key] = str(value).strip()

        if label_hint:
            for key, value in label_hint.items():
                if value is None:
                    continue
                fields.setdefault(key, str(value).strip())

        return fields

    def _build_prompt(self, document: ExtractedDocument, label_hint: Optional[Dict[str, str]]) -> str:
        hint_text = ""
        if label_hint:
            hint_entries = "\n".join(f"- {key}: {value}" for key, value in label_hint.items())
            hint_text = f"Known label values:\n{hint_entries}\n\n"
        return (
            "You are an expert assistant that extracts the following fields from battery label documents: "
            "model_name, voltage, typ_batt_capacity_wh, typ_capacity_mah, rated_capacity_mah, rated_energy_wh. "
            "Return strictly JSON with those keys (use empty string if unknown).\n\n"
            f"{hint_text}"
            f"Document text:\n{document.combined_text}\n"
        )

    async def _call_vllm(self, prompt: str) -> str:
        async with self._client_factory() as client:
            response = await client.post(
                "/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json()

        if isinstance(data, dict):
            if "output" in data and isinstance(data["output"], str):
                return data["output"]
            outputs = data.get("outputs")
            if isinstance(outputs, list) and outputs:
                text = outputs[0]
                if isinstance(text, dict):
                    text = text.get("text") or text.get("output")
                if isinstance(text, str):
                    return text
        raise RuntimeError("Unexpected response payload from vLLM endpoint")

    def _parse_payload(self, text: str) -> Dict[str, str]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise RuntimeError("vLLM output is not valid JSON")
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise RuntimeError("vLLM output is not valid JSON") from exc


def build_default_engine() -> AnalysisEngine:
    """Attempt to create a vLLM or Azure engine, fall back to heuristic when unavailable."""
    try:
        if os.getenv("VLLM_BASE_URL"):
            return VLLMAnalysisEngine()
    except Exception:
        pass
    try:
        return AzureChatAnalysisEngine()
    except Exception:
        return HeuristicAnalysisEngine()
