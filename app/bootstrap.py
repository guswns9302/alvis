from __future__ import annotations

from functools import lru_cache

from app.config import ensure_runtime_dirs, get_settings
from app.db.base import create_session_factory
from app.db.init_db import init_db
from app.services import AlvisServices


@lru_cache(maxsize=1)
def bootstrap_services() -> AlvisServices:
    settings = get_settings()
    ensure_runtime_dirs(settings)
    init_db(settings)
    session_factory = create_session_factory(settings)
    return AlvisServices(settings=settings, session_factory=session_factory)
