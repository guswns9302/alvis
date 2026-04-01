from __future__ import annotations

from pathlib import Path

import app.upgrade as upgrade_module
from app.config import Settings


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


def test_perform_upgrade_reports_daemon_version_mismatch(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "install.json").write_text("{}")

    monkeypatch.setattr(upgrade_module, "ensure_runtime_dirs", lambda settings: None)
    monkeypatch.setattr(
        upgrade_module,
        "_fetch_release",
        lambda settings, version=None: {"tag_name": "v0.2.2", "tarball_url": "https://example.com/release.tar.gz"},
    )
    monkeypatch.setattr(upgrade_module, "_download", lambda url, target: target.write_bytes(b"fake"))

    class FakeTarFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extractall(self, target):
            extracted = Path(target) / "repo"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "pyproject.toml").write_text("[project]\nname='alvis'\nversion='0.2.2'\n")

    monkeypatch.setattr(upgrade_module.tarfile, "open", lambda *args, **kwargs: FakeTarFile())
    monkeypatch.setattr(upgrade_module.shutil, "copytree", lambda src, dst: Path(dst).mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(upgrade_module.shutil, "rmtree", lambda *args, **kwargs: None)
    monkeypatch.setattr(upgrade_module.shutil, "which", lambda cmd: "/bin/launchctl" if cmd == "launchctl" else None)
    monkeypatch.setattr(upgrade_module.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(upgrade_module, "_write_wrapper", lambda settings: settings.app_home / "bin" / "alvis")
    monkeypatch.setattr(upgrade_module, "_persist_metadata", lambda settings, version, tarball_url: None)

    class FakeLaunchdManager:
        def __init__(self, settings):
            self.settings = settings

        def stop(self):
            return {"status": "stopped"}

        def start(self):
            return {"status": "started"}

    class FakeDaemonClient:
        def __init__(self, settings):
            self.settings = settings

        def health(self, workspace_root=None):
            return {"status": "ok", "version": "v0.2.0"}

    monkeypatch.setattr(upgrade_module, "LaunchdManager", FakeLaunchdManager)
    monkeypatch.setattr(upgrade_module, "DaemonClient", FakeDaemonClient)

    result = upgrade_module.perform_upgrade(settings)

    assert result["status"] == "daemon_mismatch"
    assert result["daemon_version"] == "v0.2.0"
    assert result["daemon_version_matches_target"] is False
