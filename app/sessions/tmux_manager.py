from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.logging import get_logger


class TmuxUnavailableError(RuntimeError):
    pass


class TmuxManager:
    def __init__(self, session_prefix: str):
        self.session_prefix = session_prefix
        self.log = get_logger(__name__)

    def is_available(self) -> bool:
        return shutil.which("tmux") is not None

    def team_session_name(self, team_id: str) -> str:
        return f"{self.session_prefix}-{team_id}".replace("/", "-")

    def create_team_layout(self, team_id: str, pane_count: int) -> str:
        if not self.is_available():
            raise TmuxUnavailableError("tmux is not installed or not available on PATH")
        session_name = self.team_session_name(team_id)
        if self._session_exists(session_name):
            return session_name
        self._run(["tmux", "new-session", "-d", "-s", session_name, "-n", "leader"])
        for _ in range(max(pane_count - 1, 0)):
            self._run(["tmux", "split-window", "-t", session_name, "-h"])
        self._run(["tmux", "select-layout", "-t", session_name, "tiled"])
        return session_name

    def list_panes(self, session_name: str) -> list[str]:
        if not self.is_available():
            return []
        result = self._run(
            ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_id}"],
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def send_input(self, pane_id: str, text: str) -> None:
        if not self.is_available():
            raise TmuxUnavailableError("tmux is not installed or not available on PATH")
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        try:
            self._run(["tmux", "load-buffer", str(temp_path)])
            self._run(["tmux", "paste-buffer", "-t", pane_id])
            self._run(["tmux", "send-keys", "-t", pane_id, "Enter"])
        finally:
            temp_path.unlink(missing_ok=True)

    def focus_pane(self, pane_id: str) -> None:
        self._run(["tmux", "select-pane", "-t", pane_id])

    def capture_debug_snapshot(self, pane_id: str, lines: int = 200) -> str:
        result = self._run(
            ["tmux", "capture-pane", "-t", pane_id, "-p", f"-S-{lines}"],
            check=False,
        )
        return result.stdout

    def attach(self, session_name: str) -> int:
        if not self.is_available():
            raise TmuxUnavailableError("tmux is not installed or not available on PATH")
        return subprocess.call(["tmux", "attach-session", "-t", session_name])

    def _session_exists(self, session_name: str) -> bool:
        result = self._run(["tmux", "has-session", "-t", session_name], check=False)
        return result.returncode == 0

    def _run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        self.log.info("tmux.command", cmd=cmd)
        return subprocess.run(cmd, check=check, capture_output=True, text=True)
