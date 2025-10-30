from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.jobs.models import JobEvent, JobRecord, JobStatus
from backend.jobs.repository import JobRepository
from backend.jobs.service import JobService

router = APIRouter()


def get_repository(request: Request) -> JobRepository:
    repository: JobRepository = request.app.state.job_repository
    return repository


def get_service(request: Request) -> JobService:
    service: JobService = request.app.state.job_service
    return service


def _ensure_owner(job: JobRecord, owner_id: Optional[str]) -> None:
    if owner_id is None:
        return
    if job.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Job does not belong to the specified owner")


class JobFileModel(BaseModel):
    filename: str


class CreateJobRequest(BaseModel):
    owner_id: str = Field(default="anonymous")
    source_path: str
    files: List[JobFileModel]
    parameters: dict[str, Any] = Field(default_factory=dict)


class CancelJobRequest(BaseModel):
    reason: str = Field(default="cancelled by user")
    cancelled_by: Optional[str] = None


class JobEventModel(BaseModel):
    created_at: datetime
    level: str
    message: str
    metadata: dict[str, Any]

    @classmethod
    def from_domain(cls, event: JobEvent) -> "JobEventModel":
        return cls(
            created_at=event.created_at,
            level=event.level,
            message=event.message,
            metadata=event.metadata,
        )


class JobSummary(BaseModel):
    job_id: str
    owner_id: str
    status: JobStatus
    source_path: str
    progress: float
    total_files: int
    processed_files: int
    current_file: Optional[str]
    error: Optional[str]
    download_path: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, job: JobRecord) -> "JobSummary":
        return cls(
            job_id=job.job_id,
            owner_id=job.owner_id,
            status=job.status,
            source_path=job.source_path,
            progress=job.progress,
            total_files=job.total_files,
            processed_files=job.processed_files,
            current_file=job.current_file,
            error=job.error,
            download_path=job.download_path,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class JobDetailResponse(JobSummary):
    input_manifest: list[dict[str, Any]]
    output_manifest: list[dict[str, Any]]
    events: list[JobEventModel]

    @classmethod
    def from_domain(cls, job: JobRecord, events: list[JobEvent]) -> "JobDetailResponse":
        base = JobSummary.from_domain(job)
        return cls(
            **base.model_dump(),
            input_manifest=job.input_manifest,
            output_manifest=job.output_manifest,
            events=[JobEventModel.from_domain(event) for event in events],
        )


@router.post(
    "/",
    response_model=JobSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_job(
    request: CreateJobRequest,
    repository: JobRepository = Depends(get_repository),
    service: JobService = Depends(get_service),
) -> JobSummary:
    job = service.create_job(
        owner_id=request.owner_id,
        source_path=request.source_path,
        files=[file.model_dump() for file in request.files],
        parameters=request.parameters,
    )
    return JobSummary.from_domain(job)


@router.get("/", response_model=list[JobSummary])
def list_jobs(
    owner_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    repository: JobRepository = Depends(get_repository),
) -> list[JobSummary]:
    statuses = None
    if status_filter:
        try:
            statuses = [JobStatus(status_filter)]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    jobs = repository.list_jobs(owner_id=owner_id, statuses=statuses)
    return [JobSummary.from_domain(job) for job in jobs]


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(
    job_id: str,
    repository: JobRepository = Depends(get_repository),
) -> JobDetailResponse:
    try:
        job = repository.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    events = repository.list_events(job_id)
    _ensure_owner(job, owner_id)
    return JobDetailResponse.from_domain(job, events)


@router.post("/{job_id}/cancel", response_model=JobSummary)
def cancel_job(
    job_id: str,
    request: CancelJobRequest,
    repository: JobRepository = Depends(get_repository),
    service: JobService = Depends(get_service),
    owner_id: Optional[str] = None,
) -> JobSummary:
    try:
        job = repository.cancel_job(job_id, reason=request.reason, cancelled_by=request.cancelled_by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _ensure_owner(job, owner_id)
    service.refresh_status_snapshot(job)
    return JobSummary.from_domain(job)


@router.get("/{job_id}/download")
def download_job(
    job_id: str,
    repository: JobRepository = Depends(get_repository),
    owner_id: Optional[str] = None,
) -> FileResponse:
    try:
        job = repository.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _ensure_owner(job, owner_id)
    if not job.download_path:
        raise HTTPException(status_code=404, detail="Result not ready")

    download_path = Path(job.download_path)
    if not download_path.exists():
        raise HTTPException(status_code=404, detail="Result file is missing")

    return FileResponse(
        path=download_path,
        filename=download_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    repository: JobRepository = Depends(get_repository),
    owner_id: Optional[str] = None,
    retry_ms: int = 5000,
) -> StreamingResponse:
    try:
        job = repository.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _ensure_owner(job, owner_id)

    async def event_generator() -> Any:
        last_event_id = 0
        retry_line = f"retry: {retry_ms}\n"
        while True:
            events = await asyncio.to_thread(repository.list_events, job_id)
            for event in events:
                if event.event_id <= last_event_id:
                    continue
                last_event_id = event.event_id
                payload = {
                    "event_id": event.event_id,
                    "created_at": event.created_at.isoformat(),
                    "level": event.level,
                    "message": event.message,
                    "metadata": event.metadata,
                }
                data = json.dumps(payload, ensure_ascii=False)
                yield f"id: {event.event_id}\n{retry_line}event: update\ndata: {data}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
