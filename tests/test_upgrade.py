from __future__ import annotations

from pathlib import Path

import app.upgrade as upgrade_module
from app.config import Settings


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        app_home=tmp_path / ".alvis",
        repo_root=tmp_path,
        data_dir=data_dir,
        db_path=data_dir / "alvis.db",
        log_dir=data_dir / "logs",
        runtime_dir=data_dir / "runtime",
        worktree_root=data_dir / "worktrees",
        codex_command="/usr/local/bin/codex",
        tmux_path="/opt/homebrew/bin/tmux",
    )


def test_perform_upgrade_self_heals_stale_metadata(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    persisted: list[tuple[str, str]] = []

    monkeypatch.setattr(upgrade_module, "ensure_runtime_dirs", lambda settings: None)
    monkeypatch.setattr(
        upgrade_module,
        "_fetch_release",
        lambda settings, version=None: {"tag_name": "v0.2.3", "tarball_url": "https://example.com/release.tar.gz"},
    )
    monkeypatch.setattr(
        upgrade_module,
        "inspect_installation_state",
        lambda settings: {
            "metadata_version": "v0.2.2",
            "installed_app_version": "0.2.3",
            "app_dir_exists": True,
            "wrapper_exists": True,
            "venv_entrypoint_exists": True,
        },
    )
    monkeypatch.setattr(upgrade_module.shutil, "which", lambda cmd: "/bin/launchctl" if cmd == "launchctl" else None)
    monkeypatch.setattr(
        upgrade_module,
        "_verify_daemon_version",
        lambda settings, target: {
            "daemon_restarted": False,
            "daemon_version": "0.2.3",
            "daemon_version_matches_target": True,
            "daemon_error": None,
        },
    )
    monkeypatch.setattr(
        upgrade_module,
        "_verify_sdk_installation",
        lambda settings: {
            "sdk_installed": True,
            "sdk_import_error": None,
        },
    )
    monkeypatch.setattr(
        upgrade_module,
        "_persist_metadata",
        lambda settings, version, tarball_url: persisted.append((version, tarball_url)),
    )

    result = upgrade_module.perform_upgrade(settings)

    assert result["status"] == "noop"
    assert result["metadata_updated"] is True
    assert result["install_drift_detected"] is True
    assert persisted == [("v0.2.3", "https://example.com/release.tar.gz")]


def test_perform_upgrade_reinstalls_when_installed_app_is_stale(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    installed = []
    state_calls = iter(
        [
            {
                "metadata_version": "v0.2.3",
                "installed_app_version": "0.2.2",
                "app_dir_exists": True,
                "wrapper_exists": True,
                "venv_entrypoint_exists": True,
            },
            {
                "metadata_version": "v0.2.3",
                "installed_app_version": "0.2.3",
                "app_dir_exists": True,
                "wrapper_exists": True,
                "venv_entrypoint_exists": True,
            },
        ]
    )

    monkeypatch.setattr(upgrade_module, "ensure_runtime_dirs", lambda settings: None)
    monkeypatch.setattr(
        upgrade_module,
        "_fetch_release",
        lambda settings, version=None: {"tag_name": "v0.2.3", "tarball_url": "https://example.com/release.tar.gz"},
    )
    monkeypatch.setattr(upgrade_module, "inspect_installation_state", lambda settings: next(state_calls))
    monkeypatch.setattr(upgrade_module.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(
        upgrade_module,
        "_verify_sdk_installation",
        lambda settings: {
            "sdk_installed": True,
            "sdk_import_error": None,
        },
    )
    monkeypatch.setattr(
        upgrade_module,
        "_download",
        lambda url, target: target.write_bytes(b"fake"),
    )

    class FakeTarFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extractall(self, target):
            extracted = Path(target) / "repo"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "pyproject.toml").write_text("[project]\nname='alvis'\nversion='0.2.3'\n")

    monkeypatch.setattr(upgrade_module.tarfile, "open", lambda *args, **kwargs: FakeTarFile())
    monkeypatch.setattr(
        upgrade_module,
        "_install_from_source",
        lambda settings, source_dir, version, tarball_url, codex_command=None: installed.append((Path(source_dir).name, version, tarball_url)),
    )

    result = upgrade_module.perform_upgrade(settings)

    assert result["status"] == "upgraded"
    assert installed == [("repo", "v0.2.3", "https://example.com/release.tar.gz")]
    assert result["installed_app_version"] == "0.2.3"


def test_perform_upgrade_reports_daemon_version_mismatch_after_reinstall(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    state_calls = iter(
        [
            {
                "metadata_version": None,
                "installed_app_version": None,
                "app_dir_exists": False,
                "wrapper_exists": False,
                "venv_entrypoint_exists": False,
            },
            {
                "metadata_version": "v0.2.3",
                "installed_app_version": "0.2.3",
                "app_dir_exists": True,
                "wrapper_exists": True,
                "venv_entrypoint_exists": True,
            },
        ]
    )

    monkeypatch.setattr(upgrade_module, "ensure_runtime_dirs", lambda settings: None)
    monkeypatch.setattr(
        upgrade_module,
        "_fetch_release",
        lambda settings, version=None: {"tag_name": "v0.2.3", "tarball_url": "https://example.com/release.tar.gz"},
    )
    monkeypatch.setattr(upgrade_module, "inspect_installation_state", lambda settings: next(state_calls))
    monkeypatch.setattr(upgrade_module.shutil, "which", lambda cmd: "/bin/launchctl" if cmd == "launchctl" else None)
    monkeypatch.setattr(
        upgrade_module,
        "_verify_sdk_installation",
        lambda settings: {
            "sdk_installed": True,
            "sdk_import_error": None,
        },
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

    monkeypatch.setattr(upgrade_module.tarfile, "open", lambda *args, **kwargs: FakeTarFile())
    monkeypatch.setattr(upgrade_module, "_install_from_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        upgrade_module,
        "_daemon_result",
        lambda settings, target, restart: {
            "daemon_restarted": restart,
            "daemon_version": "0.2.0",
            "daemon_version_matches_target": False,
            "daemon_error": "daemon version mismatch",
        },
    )

    result = upgrade_module.perform_upgrade(settings)

    assert result["status"] == "daemon_mismatch"
    assert result["daemon_version"] == "0.2.0"
    assert result["daemon_version_matches_target"] is False


def test_perform_upgrade_noop_when_install_and_daemon_are_aligned(monkeypatch, tmp_path):
    settings = _settings(tmp_path)

    monkeypatch.setattr(upgrade_module, "ensure_runtime_dirs", lambda settings: None)
    monkeypatch.setattr(
        upgrade_module,
        "_fetch_release",
        lambda settings, version=None: {"tag_name": "v0.2.3", "tarball_url": "https://example.com/release.tar.gz"},
    )
    monkeypatch.setattr(
        upgrade_module,
        "inspect_installation_state",
        lambda settings: {
            "metadata_version": "v0.2.3",
            "installed_app_version": "0.2.3",
            "app_dir_exists": True,
            "wrapper_exists": True,
            "venv_entrypoint_exists": True,
        },
    )
    monkeypatch.setattr(upgrade_module.shutil, "which", lambda cmd: "/bin/launchctl" if cmd == "launchctl" else None)
    monkeypatch.setattr(
        upgrade_module,
        "_verify_daemon_version",
        lambda settings, target: {
            "daemon_restarted": False,
            "daemon_version": "0.2.3",
            "daemon_version_matches_target": True,
            "daemon_error": None,
        },
    )
    monkeypatch.setattr(
        upgrade_module,
        "_verify_sdk_installation",
        lambda settings: {
            "sdk_installed": True,
            "sdk_import_error": None,
        },
    )

    result = upgrade_module.perform_upgrade(settings)

    assert result["status"] == "noop"
    assert result["metadata_updated"] is False
    assert result["install_drift_detected"] is False
    assert result["daemon_version"] == "0.2.3"


def test_perform_upgrade_reports_sdk_missing_after_reinstall(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    state_calls = iter(
        [
            {
                "metadata_version": None,
                "installed_app_version": None,
                "app_dir_exists": False,
                "wrapper_exists": False,
                "venv_entrypoint_exists": False,
            },
            {
                "metadata_version": "v0.2.3",
                "installed_app_version": "0.2.3",
                "app_dir_exists": True,
                "wrapper_exists": True,
                "venv_entrypoint_exists": True,
            },
        ]
    )

    monkeypatch.setattr(upgrade_module, "ensure_runtime_dirs", lambda settings: None)
    monkeypatch.setattr(
        upgrade_module,
        "_fetch_release",
        lambda settings, version=None: {"tag_name": "v0.2.3", "tarball_url": "https://example.com/release.tar.gz"},
    )
    monkeypatch.setattr(upgrade_module, "inspect_installation_state", lambda settings: next(state_calls))
    monkeypatch.setattr(upgrade_module.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(
        upgrade_module,
        "_verify_sdk_installation",
        lambda settings: {
            "sdk_installed": False,
            "sdk_import_error": "No module named 'openai'",
        },
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

    monkeypatch.setattr(upgrade_module.tarfile, "open", lambda *args, **kwargs: FakeTarFile())
    monkeypatch.setattr(upgrade_module, "_install_from_source", lambda *args, **kwargs: None)

    result = upgrade_module.perform_upgrade(settings)

    assert result["status"] == "sdk_missing"
    assert result["sdk_installed"] is False
    assert "openai" in result["sdk_import_error"]
