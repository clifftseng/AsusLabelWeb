import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / 'backend'
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from analysis_components import HeuristicAnalysisEngine, PDFDocumentLoader
from document_analysis import LabelAnalysisService
from settings import ensure_env_loaded


@dataclass
class PageResult:
    path: Path
    duration: float
    fields: dict
    messages: List[str]


async def analyse_directory(directory: Path, use_aio: bool = True) -> List[PageResult]:
    ensure_env_loaded()
    if use_aio:
        os.environ["DI_USE_AIO"] = "1"
    loader = PDFDocumentLoader(max_pages=5)
    engine = HeuristicAnalysisEngine()
    service = LabelAnalysisService(
        document_loader=loader,
        analysis_engine=engine,
        format_repository=None,
    )

    results: List[PageResult] = []
    try:
        pdf_files = sorted(directory.glob("*.pdf"))
        if not pdf_files:
            print(f"No PDF files found in {directory}")
            return results

        for pdf_path in pdf_files:
            start = time.perf_counter()
            fields, messages = await service.analyse(pdf_path)
            duration = time.perf_counter() - start
            results.append(PageResult(pdf_path, duration, fields, messages))
            summary = ", ".join(
                f"{key}={value!r}"
                for key, value in fields.items()
                if key in {"model_name", "voltage", "typ_batt_capacity_wh"}
            )
            print(f"[{duration:6.2f}s] {pdf_path.name}: {summary}")
    finally:
        await service.aclose()

    return results


async def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test_pdf"
    print(f"Analysing PDFs in {target}")
    start = time.perf_counter()
    results = await analyse_directory(target)
    duration = time.perf_counter() - start
    print(f"\nProcessed {len(results)} PDFs in {duration:.2f}s total.")


if __name__ == "__main__":
    asyncio.run(main())
