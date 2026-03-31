from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.config import ensure_runtime_dirs, get_settings
from app.db.base import create_session_factory
from app.db.init_db import init_db
from app.logging import configure_logging
from app.services import AlvisServices


@lru_cache(maxsize=32)
def _bootstrap_services_cached(workspace_key: str) -> AlvisServices:
    configure_logging()
    settings = get_settings(workspace_key or None)
    ensure_runtime_dirs(settings)
    init_db(settings)
    session_factory = create_session_factory(settings)
    return AlvisServices(settings=settings, session_factory=session_factory)


def bootstrap_services(workspace_root: str | Path | None = None) -> AlvisServices:
    workspace_key = ""
    if workspace_root is not None:
        workspace_key = str(Path(workspace_root).expanduser().resolve())
    return _bootstrap_services_cached(workspace_key)
