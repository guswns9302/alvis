from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.logging import get_logger


class TmuxUnavailableError(RuntimeError):
    pass


class TmuxManager:
    FALLBACK_PATHS = (
        "/opt/homebrew/bin/tmux",
        "/usr/local/bin/tmux",
    )

    def __init__(self, session_prefix: str, tmux_path: str | None = None):
        self.session_prefix = session_prefix
        self.tmux_path = tmux_path
        self.log = get_logger(__name__)

    def executable(self) -> str | None:
        explicit = self.tmux_path or os.getenv("ALVIS_TMUX_PATH")
        if explicit and Path(explicit).exists():
            return explicit
        resolved = shutil.which("tmux")
        if resolved:
            return resolved
        for candidate in self.FALLBACK_PATHS:
            if Path(candidate).exists():
                return candidate
        return None

    def is_available(self) -> bool:
        return self.executable() is not None

    def _cmd(self, *args: str) -> list[str]:
        executable = self.executable()
        if not executable:
            raise TmuxUnavailableError("tmux is not installed or not available on PATH")
        return [executable, *args]

    def team_session_name(self, team_id: str) -> str:
        return f"{self.session_prefix}-{team_id}".replace("/", "-")

    def create_team_layout(self, team_id: str, pane_count: int, commands: list[str] | None = None) -> str:
        if not self.is_available():
            raise TmuxUnavailableError("tmux is not installed or not available on PATH")
        session_name = self.team_session_name(team_id)
        if self._session_exists(session_name):
            return session_name
        first_command = commands[0] if commands else None
        cmd = self._cmd("new-session", "-d", "-s", session_name, "-n", "leader")
        if first_command:
            cmd.append(first_command)
        self._run(cmd)
        if pane_count >= 2:
            worker_cmd = commands[1] if commands and len(commands) > 1 else None
            split_cmd = self._cmd("split-window", "-d", "-h", "-t", f"{session_name}:0", "-P", "-F", "#{pane_id}")
            if worker_cmd:
                split_cmd.append(worker_cmd)
            self._run(split_cmd)

        self._run(self._cmd("set-option", "-t", session_name, "pane-border-status", "top"), check=False)
        self._run(self._cmd("set-option", "-t", session_name, "pane-border-format", "#{pane_title}"), check=False)
        for pane_id, title in zip(self.list_panes(session_name), ["leader", "workers"], strict=False):
            self._run(self._cmd("select-pane", "-t", pane_id, "-T", title), check=False)
        return session_name

    def list_panes(self, session_name: str) -> list[str]:
        if not self.is_available():
            return []
        result = self._run(
            self._cmd("list-panes", "-t", session_name, "-F", "#{pane_left}:#{pane_top}:#{pane_id}"),
            check=False,
        )
        if result.returncode != 0:
            return []
        panes: list[tuple[int, int, str]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            left, top, pane_id = line.split(":", 2)
            panes.append((int(left), int(top), pane_id))
        panes.sort(key=lambda item: (item[0], item[1]))
        return [pane_id for _, _, pane_id in panes]

    def send_input(self, pane_id: str, text: str) -> None:
        if not self.is_available():
            raise TmuxUnavailableError("tmux is not installed or not available on PATH")
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        try:
            self._run(self._cmd("load-buffer", str(temp_path)))
            self._run(self._cmd("paste-buffer", "-t", pane_id))
            self._run(self._cmd("send-keys", "-t", pane_id, "Enter"))
        finally:
            temp_path.unlink(missing_ok=True)

    def focus_pane(self, pane_id: str) -> None:
        self._run(self._cmd("select-pane", "-t", pane_id))

    def pipe_pane_to_file(self, pane_id: str, log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(self._cmd("pipe-pane", "-o", "-t", pane_id, f"cat >> {log_path}"), check=False)
        if result.returncode != 0:
            self.log.warning("tmux.pipe_pane_failed", pane_id=pane_id, log_path=str(log_path), stderr=result.stderr)

    def capture_debug_snapshot(self, pane_id: str, lines: int = 200) -> str:
        result = self._run(
            self._cmd("capture-pane", "-t", pane_id, "-p", f"-S-{lines}"),
            check=False,
        )
        return result.stdout

    def pane_exists(self, pane_id: str) -> bool:
        result = self._run(self._cmd("list-panes", "-a", "-F", "#{pane_id}"), check=False)
        if result.returncode != 0:
            return False
        return pane_id in {line.strip() for line in result.stdout.splitlines()}

    def attach(self, session_name: str) -> int:
        if not self.is_available():
            raise TmuxUnavailableError("tmux is not installed or not available on PATH")
        return subprocess.call(self._cmd("attach-session", "-t", session_name))

    def kill_session(self, session_name: str) -> None:
        self._run(self._cmd("kill-session", "-t", session_name), check=False)

    def _session_exists(self, session_name: str) -> bool:
        result = self._run(self._cmd("has-session", "-t", session_name), check=False)
        return result.returncode == 0

    def _run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        self.log.debug("tmux.command", cmd=cmd)
        return subprocess.run(cmd, check=check, capture_output=True, text=True)
