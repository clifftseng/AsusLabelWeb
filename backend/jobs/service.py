from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .models import JobRecord
from .repository import JobRepository


class JobService:
    """Domain service orchestrating job creation and filesystem management."""

    def __init__(self, *, repository: JobRepository, storage_root: Path | str) -> None:
        self.repository = repository
        self.storage_root = Path(storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        *,
        owner_id: str,
        source_path: str,
        files: Iterable[Mapping[str, Any]],
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> JobRecord:
        source_dir = Path(source_path)
        manifest: list[dict[str, Any]] = []
        for item in files:
            filename = item.get("filename")
            if not filename:
                raise ValueError("Each file entry must include a 'filename' key.")
            source_file = source_dir / filename
            if not source_file.exists():
                raise FileNotFoundError(f"File not found: {source_file}")
            manifest.append(
                {
                    "filename": filename,
                    "source_path": str(source_file),
                    "size": source_file.stat().st_size,
                }
            )

        payload = {
            "source_path": str(source_dir),
            "files": manifest,
            "parameters": dict(parameters or {}),
            "total_files": len(manifest),
        }
        job = self.repository.enqueue_job(owner_id=owner_id, payload=payload)
        job_dir = self.job_directory(job.job_id)
        self._initialise_directories(job_dir)
        self._copy_inputs(source_dir, job_dir / "input", manifest)
        self._write_status_snapshot(job_dir, job)
        return job

    def job_directory(self, job_id: str) -> Path:
        job_dir = self.storage_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def cleanup_inputs(self, job_id: str) -> None:
        job_dir = self.job_directory(job_id)
        input_dir = job_dir / "input"
        if not input_dir.exists():
            return
        for pdf in input_dir.glob("*.pdf"):
            pdf.unlink(missing_ok=True)

    def refresh_status_snapshot(self, job: JobRecord) -> None:
        job_dir = self.job_directory(job.job_id)
        self._write_status_snapshot(job_dir, job)

    def _initialise_directories(self, job_dir: Path) -> None:
        for sub in ("input", "working", "output", "logs"):
            (job_dir / sub).mkdir(parents=True, exist_ok=True)

    def _copy_inputs(
        self,
        source_dir: Path,
        target_dir: Path,
        manifest: Iterable[Mapping[str, Any]],
    ) -> None:
        for item in manifest:
            filename = item["filename"]
            shutil.copy2(source_dir / filename, target_dir / filename)

    def _write_status_snapshot(self, job_dir: Path, job: JobRecord) -> None:
        status_path = job_dir / "status.json"
        data = {
            "job_id": job.job_id,
            "status": job.status.value,
            "owner_id": job.owner_id,
            "total_files": job.total_files,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        status_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
