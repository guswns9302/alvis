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
            "version": cli_module.__version__,
            "daemon_codex_command": "/usr/local/bin/codex",
            "daemon_workspace_root": str(workspace_root or "/tmp/workspace"),
            "daemon_data_dir": "/tmp/data",
            "daemon_db_path": "/tmp/data/alvis.db",
            "daemon_runtime_dir": "/tmp/data/runtime",
            "daemon_team_count": 1,
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
            "action": "created",
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
    )


def test_doctor_prints_daemon_runtime_details(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "get_settings", lambda workspace_root=None: _settings(tmp_path))
    monkeypatch.setattr(cli_module, "_daemon_client", lambda: FakeDaemonClient())
    monkeypatch.setattr(
        cli_module,
        "inspect_installation_state",
        lambda settings: {
            "metadata_version": "v0.2.3",
            "installed_app_version": "0.2.3",
            "app_dir_exists": True,
            "wrapper_exists": True,
            "venv_entrypoint_exists": True,
        },
    )
    monkeypatch.setattr(
        cli_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    result = runner.invoke(cli_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "daemon codex_command: /usr/local/bin/codex" in result.output
    assert f"daemon version: {cli_module.__version__}" in result.output
    assert "daemon db_path: /tmp/data/alvis.db" in result.output
    assert "workspace teams: 1" in result.output
    assert "recommended action: run `alvis start`" in result.output


def test_doctor_warns_when_install_state_drifts(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "get_settings", lambda workspace_root=None: _settings(tmp_path))
    monkeypatch.setattr(cli_module, "_daemon_client", lambda: FakeDaemonClient())
    monkeypatch.setattr(
        cli_module,
        "inspect_installation_state",
        lambda settings: {
            "metadata_version": "v0.2.2",
            "installed_app_version": "0.2.3",
            "app_dir_exists": True,
            "wrapper_exists": True,
            "venv_entrypoint_exists": True,
        },
    )
    monkeypatch.setattr(cli_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result = runner.invoke(cli_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "install metadata version: v0.2.2" in result.output
    assert "installed app version: 0.2.3" in result.output
    assert "install drift detected" in result.output
    assert "recommended action: run `alvis upgrade` to repair the installed app state" in result.output


def test_start_surfaces_conflict_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_services", lambda workspace_root=None: SimpleNamespace(start_or_attach_default_team=lambda: (_ for _ in ()).throw(ValueError("team demo already exists"))))

    result = runner.invoke(cli_module.app, ["start"])

    assert result.exit_code == 1
    assert "team demo already exists" in result.output


def test_start_uses_local_services_without_daemon(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_direct_mode", lambda: False)
    monkeypatch.setattr(cli_module, "_ensure_daemon_running", lambda: (_ for _ in ()).throw(AssertionError("daemon should not be used")))
    monkeypatch.setattr(
        cli_module,
        "_services",
        lambda workspace_root=None: SimpleNamespace(
            start_or_attach_default_team=lambda: calls.append("start") or {"team_id": "team-demo", "action": "created"}
        ),
    )

    result = runner.invoke(cli_module.app, ["start"])

    assert result.exit_code == 0
    assert calls == ["start"]


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


def test_doctor_warns_when_daemon_version_mismatches(monkeypatch, tmp_path):
    class MismatchClient(FakeDaemonClient):
        def health(self, workspace_root=None):
            payload = super().health(workspace_root)
            payload["version"] = "0.1.0"
            return payload

    monkeypatch.setattr(cli_module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "get_settings", lambda workspace_root=None: _settings(tmp_path))
    monkeypatch.setattr(cli_module, "_daemon_client", lambda: MismatchClient())
    monkeypatch.setattr(
        cli_module,
        "inspect_installation_state",
        lambda settings: {
            "metadata_version": "v0.2.3",
            "installed_app_version": "0.2.3",
            "app_dir_exists": True,
            "wrapper_exists": True,
            "venv_entrypoint_exists": True,
        },
    )
    monkeypatch.setattr(cli_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result = runner.invoke(cli_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "daemon version mismatch" in result.output
    assert "recommended action: run `alvis daemon restart` or `alvis upgrade` again" in result.output
