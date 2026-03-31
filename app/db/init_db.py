from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from app.db.base import create_sqlite_engine
from app.db.models import Base


REQUIRED_COLUMNS = {
    "agents": {"role_alias"},
    "tasks": {"target_role_alias", "owned_paths", "task_type", "parent_task_id"},
    "interactions": {"kind", "status", "payload"},
}


def _validate_schema(engine) -> None:
    inspector = inspect(engine)
    for table_name, required_columns in REQUIRED_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing = required_columns - existing_columns
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise RuntimeError(
                "현재 로컬 DB 스키마가 오래되었습니다. "
                f"`{table_name}` 테이블에 필요한 컬럼({missing_list})이 없습니다. "
                "`.alvis/alvis.db`를 백업한 뒤 삭제하고 다시 실행하세요."
            )


def init_db(settings) -> None:
    engine = create_sqlite_engine(settings)
    try:
        Base.metadata.create_all(engine)
        _validate_schema(engine)
    except OperationalError as exc:
        message = str(exc).lower()
        if "disk i/o error" in message:
            raise RuntimeError(
                "로컬 Alvis DB를 초기화하는 중 SQLite disk I/O error가 발생했습니다. "
                f"`{settings.db_path}`와 함께 남아 있는 `-wal`/`-shm` 파일이 손상되었을 수 있습니다. "
                "Alvis를 모두 종료한 뒤 DB 파일을 백업하거나 삭제하고 다시 실행하세요."
            ) from exc
        raise
