from __future__ import annotations

from app.db.base import create_sqlite_engine
from app.db.models import Base


def init_db(settings) -> None:
    engine = create_sqlite_engine(settings)
    Base.metadata.create_all(engine)
