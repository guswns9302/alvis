from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo_root: Path
    data_dir: Path
    db_path: Path
    log_dir: Path
    runtime_dir: Path
    worktree_root: Path
    tmux_session_prefix: str = "alvis"
    default_worker_count: int = 2
    codex_command: str = "codex"
    heartbeat_timeout_seconds: int = 120
    review_retry_threshold: int = 2


def get_settings() -> Settings:
    repo_root = Path(os.getenv("ALVIS_REPO_ROOT", Path(__file__).resolve().parent.parent))
    data_dir = Path(os.getenv("ALVIS_DATA_DIR", repo_root / ".alvis"))
    log_dir = Path(os.getenv("ALVIS_LOG_DIR", data_dir / "logs"))
    runtime_dir = Path(os.getenv("ALVIS_RUNTIME_DIR", data_dir / "runtime"))
    worktree_root = Path(os.getenv("ALVIS_WORKTREE_ROOT", repo_root / ".worktrees"))
    return Settings(
        repo_root=repo_root,
        data_dir=data_dir,
        db_path=Path(os.getenv("ALVIS_DB_PATH", data_dir / "alvis.db")),
        log_dir=log_dir,
        runtime_dir=runtime_dir,
        worktree_root=worktree_root,
        tmux_session_prefix=os.getenv("ALVIS_TMUX_PREFIX", "alvis"),
        codex_command=os.getenv("ALVIS_CODEX_COMMAND", "codex"),
    )


def ensure_runtime_dirs(settings: Settings) -> None:
    for path in (
        settings.data_dir,
        settings.log_dir,
        settings.runtime_dir,
        settings.worktree_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
