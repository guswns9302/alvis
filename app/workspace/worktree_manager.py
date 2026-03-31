from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.logging import get_logger


@dataclass
class WorktreeState:
    path: Path
    branch: str | None
    exists: bool
    clean: bool
    changed_files: list[str]


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

    def status_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]

    def changed_files(self, path: Path) -> list[str]:
        files = []
        for line in self.status_lines(path):
            if len(line) < 4:
                continue
            file_path = line[3:].strip()
            if " -> " in file_path:
                file_path = file_path.split(" -> ", 1)[1].strip()
            files.append(file_path)
        return files

    def current_branch(self, path: Path) -> str | None:
        if not path.exists():
            return None
        result = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        )
        branch = result.stdout.strip()
        return branch or None

    def inspect(self, path: Path) -> WorktreeState:
        exists = path.exists()
        changed_files = self.changed_files(path) if exists else []
        return WorktreeState(
            path=path,
            branch=self.current_branch(path) if exists else None,
            exists=exists,
            clean=not changed_files,
            changed_files=changed_files,
        )

    def remove(self, path: Path) -> None:
        if not path.exists():
            return
        cmd = [
            "git",
            "-C",
            str(self.repo_root),
            "worktree",
            "remove",
            "--force",
            str(path),
        ]
        self.log.info("worktree.remove", cmd=cmd)
        subprocess.run(cmd, check=True, capture_output=True, text=True)
