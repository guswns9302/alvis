from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import app.cli as cli_module
from app.config import Settings
from app.daemon_client import DaemonHttpError


runner = CliRunner()


class FakeDaemonClient:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = []

    def health(self, workspace_root=None):
        return {
            "status": "ok",
            "daemon_tmux_path": "/opt/homebrew/bin/tmux",
            "daemon_tmux_available": True,
            "daemon_codex_command": "/usr/local/bin/codex",
            "daemon_workspace_root": str(workspace_root or "/tmp/workspace"),
            "daemon_data_dir": "/tmp/data",
        }

    def with_workspace(self, workspace_root=None):
        return {"workspace_root": str(workspace_root or "/tmp/workspace")}

    def request_json(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if self.error:
            raise self.error
        if args[1] == "/clean":
            return {
                "removed_teams": [],
                "skipped_teams": [],
                "removed_count": 0,
                "skipped_count": 0,
            }
        return {
            "team_id": "team-demo",
            "session_name": "alvis-demo",
            "action": "created",
            "start_result": {"all_ready": True, "session_name": "alvis-demo"},
        }


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        repo_root=tmp_path,
        data_dir=data_dir,
        db_path=data_dir / "alvis.db",
        log_dir=data_dir / "logs",
        runtime_dir=data_dir / "runtime",
        worktree_root=data_dir / "worktrees",
        codex_command="/usr/local/bin/codex",
        tmux_path="/opt/homebrew/bin/tmux",
    )


def test_doctor_prints_daemon_runtime_details(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "get_settings", lambda workspace_root=None: _settings(tmp_path))
    monkeypatch.setattr(cli_module, "_daemon_client", lambda: FakeDaemonClient())
    monkeypatch.setattr(
        cli_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    result = runner.invoke(cli_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "daemon tmux_path: /opt/homebrew/bin/tmux" in result.output
    assert "daemon codex_command: /usr/local/bin/codex" in result.output
    assert "daemon tmux: ok" in result.output


def test_start_surfaces_conflict_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_direct_mode", lambda: False)
    monkeypatch.setattr(cli_module, "_ensure_daemon_running", lambda: FakeDaemonClient(error=DaemonHttpError(409, {"error_code": "team_exists", "detail": "team demo already exists"})))
    monkeypatch.setattr(cli_module, "_services", lambda workspace_root=None: SimpleNamespace(attach_tmux=lambda team_id: 0))

    result = runner.invoke(cli_module.app, ["start"])

    assert result.exit_code == 1
    assert "team demo already exists" in result.output


def test_start_uses_longer_daemon_timeout(monkeypatch, tmp_path):
    client = FakeDaemonClient()
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_direct_mode", lambda: False)
    monkeypatch.setattr(cli_module, "_ensure_daemon_running", lambda: client)
    monkeypatch.setattr(cli_module, "_services", lambda workspace_root=None: SimpleNamespace(attach_tmux=lambda team_id: 0))

    result = runner.invoke(cli_module.app, ["start"])

    assert result.exit_code == 0
    assert client.calls
    assert client.calls[0]["kwargs"]["timeout"] == 30


def test_clean_uses_longer_daemon_timeout(monkeypatch, tmp_path):
    client = FakeDaemonClient()
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_direct_mode", lambda: False)
    monkeypatch.setattr(cli_module, "_ensure_daemon_running", lambda: client)

    result = runner.invoke(cli_module.app, ["clean"])

    assert result.exit_code == 0
    assert client.calls
    assert client.calls[0]["args"][1] == "/clean"
    assert client.calls[0]["kwargs"]["timeout"] == 30
