from __future__ import annotations

import subprocess
from pathlib import Path

from app.logging import get_logger


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_root: Path):
        self.repo_root = repo_root
        self.worktree_root = worktree_root
        self.log = get_logger(__name__)

    def branch_name(self, team_id: str, agent_id: str) -> str:
        return f"alvis/{team_id}/{agent_id}"

    def worktree_path(self, team_id: str, agent_id: str) -> Path:
        return self.worktree_root / team_id / agent_id

    def ensure_worktree(self, team_id: str, agent_id: str) -> tuple[Path, str]:
        path = self.worktree_path(team_id, agent_id)
        branch = self.branch_name(team_id, agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path, branch
        cmd = [
            "git",
            "-C",
            str(self.repo_root),
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
        ]
        self.log.info("worktree.create", cmd=cmd)
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return path, branch

    def diff_summary(self, path: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
