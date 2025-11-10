from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.api.jobs import router as jobs_router
from backend.jobs.repository import JobRepository
from backend.jobs.service import JobService
from backend.jobs.worker import JobWorker
from backend.processors import AnalysisJobProcessor
from backend.settings import AppSettings, ensure_env_loaded, get_settings

logger = logging.getLogger(__name__)

ensure_env_loaded()


class ListPDFsRequest(BaseModel):
    path: str


class PDFFile(BaseModel):
    id: int
    filename: str


def create_app(*, settings: AppSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    repository = JobRepository(settings.job_queue_url)
    service = JobService(repository=repository, storage_root=settings.job_storage_root)

    app = FastAPI()
    app.state.settings = settings
    app.state.job_repository = repository
    app.state.job_service = service
    app.state.worker_tasks: list[tuple[JobWorker, asyncio.Task]] = []
    app.state.processor_factory = lambda: AnalysisJobProcessor()

    origins = [
        "http://localhost",
        "http://localhost:80",
        "http://localhost:3000",
        "http://localhost:8080",
        "http://10.100.101.57",
        "http://10.100.101.57:80",
        "http://10.100.101.57:8080",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(jobs_router, prefix="/api/jobs", tags=["jobs"])

    @app.on_event("startup")
    async def _startup_workers() -> None:
        if settings.job_max_workers <= 0:
            return
        for index in range(settings.job_max_workers):
            worker = JobWorker(
                repository=repository,
                service=service,
                processor=app.state.processor_factory(),
                worker_id=f"worker-{index+1}",
                poll_interval=1.0,
            )
            task = asyncio.create_task(worker.run_forever())
            app.state.worker_tasks.append((worker, task))
        logger.info("Started %s worker(s)", settings.job_max_workers)

    @app.on_event("shutdown")
    async def _shutdown_workers() -> None:
        for worker, task in app.state.worker_tasks:
            worker.stop()
        for worker, task in app.state.worker_tasks:
            await task
        app.state.worker_tasks.clear()
        repository.close()
        logger.info("Workers stopped and repository connection closed.")

    @app.get("/")
    def read_root() -> Dict[str, str]:
        return {"message": "Welcome to the ASUS Label Analysis Backend!"}

    @app.post("/api/list-pdfs", response_model=List[PDFFile])
    def list_pdfs(request: ListPDFsRequest) -> List[PDFFile]:
        target_path = Path(request.path)
        if not target_path.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {request.path}")
        if not target_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {request.path}")

        pdf_files: List[PDFFile] = []
        try:
            entries = sorted(
                entry for entry in target_path.iterdir()
                if entry.is_file() and entry.suffix.lower() == ".pdf"
            )
            for index, entry in enumerate(entries, start=1):
                pdf_files.append(PDFFile(id=index, filename=entry.name))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Error listing files: {exc}") from exc
        return pdf_files

    return app


app = create_app()


__all__ = ["app", "create_app"]
