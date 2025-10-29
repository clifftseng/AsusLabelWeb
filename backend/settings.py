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

