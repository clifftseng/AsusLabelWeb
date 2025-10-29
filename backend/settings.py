from __future__ import annotations

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

    log_file_path = Path(__file__).resolve().parent / "backend.log"
    
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

