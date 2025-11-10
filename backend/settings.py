from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import find_dotenv, load_dotenv

_ENV_LOADED = False


def ensure_env_loaded(*, env_path: Optional[str | Path] = None, force: bool = False) -> None:
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return

    path = None
    if env_path is not None:
        path = Path(env_path)
    else:
        found = find_dotenv(usecwd=True)
        if found:
            path = Path(found)

    if path and path.exists():
        load_dotenv(dotenv_path=path, override=False)

    _ENV_LOADED = True

    configure_logging()

def configure_logging() -> None:
    import logging
    from logging import handlers # Import handlers module
    from pathlib import Path

    log_dir = Path(__file__).resolve().parent / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir / "backend.log"
    
    # Create a TimedRotatingFileHandler for daily rotation
    # 'midnight' means rotate at midnight, '1' means rotate every day
    # backupCount=7 means keep 7 days of backup logs
    file_handler = handlers.TimedRotatingFileHandler(
        log_file_path, when='midnight', interval=1, backupCount=7, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    # Create a formatter and set it for the handler
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG) # Set root logger level to DEBUG

    # Add the file handler to the root logger
    root_logger.addHandler(file_handler)

    # Add a stream handler for console output
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO) # Console output can be INFO or higher
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # Prevent duplicate logs if run multiple times (check for handlers before adding)
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)


@dataclass(slots=True)
class AppSettings:
    job_queue_url: str = os.getenv("JOB_QUEUE_URL", "sqlite:///backend/job_queue.db")
    job_storage_root: Path = Path(
        os.getenv(
            "JOB_STORAGE_ROOT",
            Path(__file__).resolve().parent / "job_runs",
        )
    )
    job_max_workers: int = int(os.getenv("JOB_MAX_WORKERS", "2"))
    job_heartbeat_sec: int = int(os.getenv("JOB_HEARTBEAT_SEC", "15"))
    job_stuck_timeout_sec: int = int(os.getenv("JOB_STUCK_TIMEOUT_SEC", "300"))
    job_cleanup_after_sec: int = int(os.getenv("JOB_CLEANUP_AFTER_SEC", "43200"))
    job_failed_retention_sec: int = int(os.getenv("JOB_FAILED_RETENTION_SEC", "172800"))
    job_sse_retry_ms: int = int(os.getenv("JOB_SSE_RETRY_MS", "5000"))
    job_export_timezone: str = os.getenv("JOB_EXPORT_TIMEZONE", "UTC")


_SETTINGS: Optional[AppSettings] = None


def get_settings() -> AppSettings:
    global _SETTINGS
    ensure_env_loaded()
    if _SETTINGS is None:
        _SETTINGS = AppSettings()
    return _SETTINGS

