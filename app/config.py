from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_home: Path = Field(default_factory=lambda: Path.home() / ".alvis")
    repo_root: Path
    workspace_id: str = ""
    data_dir: Path
    db_path: Path
    log_dir: Path
    runtime_dir: Path
    worktree_root: Path
    tmux_session_prefix: str = "alvis"
    default_worker_count: int = 2
    codex_command: str = "codex"
    tmux_path: str | None = None
    heartbeat_timeout_seconds: int = 120
    review_retry_threshold: int = 2
    daemon_host: str = "127.0.0.1"
    daemon_port: int = 35731
    release_repo: str = "guswns9302/alvis"
    launchd_label: str = "com.alvis.daemon"

    @model_validator(mode="after")
    def _fill_workspace_metadata(self) -> "Settings":
        if self.workspace_id:
            return self
        object.__setattr__(self, "workspace_id", _workspace_id(self.repo_root))
        return self


def _workspace_id(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:12]


def get_settings(workspace_root: str | Path | None = None) -> Settings:
    app_home = Path(os.getenv("ALVIS_HOME", Path.home() / ".alvis")).expanduser()
    default_workspace = workspace_root or os.getenv("ALVIS_WORKSPACE_ROOT") or os.getenv("ALVIS_REPO_ROOT") or Path.cwd()
    repo_root = Path(default_workspace).expanduser().resolve()
    workspace_id = _workspace_id(repo_root)
    data_dir = Path(os.getenv("ALVIS_DATA_DIR", app_home / "data" / "workspaces" / workspace_id))
    log_dir = Path(os.getenv("ALVIS_LOG_DIR", data_dir / "logs"))
    runtime_dir = Path(os.getenv("ALVIS_RUNTIME_DIR", data_dir / "runtime"))
    worktree_root = Path(os.getenv("ALVIS_WORKTREE_ROOT", data_dir / "worktrees"))
    return Settings(
        app_home=app_home,
        repo_root=repo_root,
        workspace_id=workspace_id,
        data_dir=data_dir,
        db_path=Path(os.getenv("ALVIS_DB_PATH", data_dir / "alvis.db")),
        log_dir=log_dir,
        runtime_dir=runtime_dir,
        worktree_root=worktree_root,
        tmux_session_prefix=os.getenv("ALVIS_TMUX_PREFIX", "alvis"),
        codex_command=os.getenv("ALVIS_CODEX_COMMAND", "codex"),
        tmux_path=os.getenv("ALVIS_TMUX_PATH"),
        daemon_host=os.getenv("ALVIS_DAEMON_HOST", "127.0.0.1"),
        daemon_port=int(os.getenv("ALVIS_DAEMON_PORT", "35731")),
        release_repo=os.getenv("ALVIS_RELEASE_REPO", "guswns9302/alvis"),
        launchd_label=os.getenv("ALVIS_LAUNCHD_LABEL", "com.alvis.daemon"),
    )


def ensure_runtime_dirs(settings: Settings) -> None:
    for path in (
        settings.app_home,
        settings.data_dir,
        settings.log_dir,
        settings.runtime_dir,
        settings.worktree_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
