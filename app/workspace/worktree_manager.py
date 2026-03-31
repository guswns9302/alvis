from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.logging import get_logger


@dataclass
class WorkspaceState:
    path: Path
    exists: bool
    files: list[str]


class WorktreeManager:
    """Shared-root workspace helper.

    The class name stays the same to keep import churn low while the product
    moves away from Git/worktree-based isolation.
    """

    def __init__(self, repo_root: Path, worktree_root: Path):
        self.repo_root = repo_root
        self.runtime_root = worktree_root
        self.log = get_logger(__name__)

    def shared_root(self) -> Path:
        return self.repo_root

    def ensure_worktree(self, team_id: str, agent_id: str) -> tuple[Path, str | None]:
        del team_id, agent_id
        return self.repo_root, None

    def inspect_runtime_dir(self, team_id: str) -> WorkspaceState:
        path = self.runtime_root / team_id
        files = []
        if path.exists():
            files = sorted(str(item.relative_to(path)) for item in path.rglob("*") if item.is_file())
        return WorkspaceState(path=path, exists=path.exists(), files=files)

    def remove_runtime_dir(self, team_id: str) -> bool:
        path = self.runtime_root / team_id
        if not path.exists():
            return False
        shutil.rmtree(path)
        return True
