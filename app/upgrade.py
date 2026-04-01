from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

from app.config import Settings, ensure_runtime_dirs
from app.install_paths import (
    install_app_dir,
    install_bin_dir,
    inspect_installation_state,
    install_metadata_path,
    install_root,
    install_venv_dir,
    install_venv_entrypoint_path,
    install_wrapper_path,
)
from app.launchd import LaunchdManager
from app.daemon_client import DaemonClient, DaemonUnavailableError
from app.version import __version__


def _github_release_url(settings: Settings, version: str | None = None) -> str:
    if version:
        return f"https://api.github.com/repos/{settings.release_repo}/releases/tags/{version}"
    return f"https://api.github.com/repos/{settings.release_repo}/releases/latest"


def _fetch_release(settings: Settings, version: str | None = None) -> dict:
    with request.urlopen(_github_release_url(settings, version), timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url: str, target: Path) -> None:
    with request.urlopen(url, timeout=30) as response:
        target.write_bytes(response.read())


def _normalize_version(value: str | None) -> str | None:
    if value is None:
        return None
    return value[1:] if value.startswith("v") else value


def _write_wrapper(settings: Settings, *, codex_command: str | None = None) -> Path:
    bin_dir = install_bin_dir(settings)
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = install_wrapper_path(settings)
    exports = [
        f'export ALVIS_HOME="{settings.app_home}"\n',
    ]
    if codex_command or settings.codex_command:
        exports.append(f'export ALVIS_CODEX_COMMAND="{codex_command or settings.codex_command}"\n')
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'set -euo pipefail\n'
        + "".join(exports)
        +
        f'exec "{install_venv_dir(settings) / "bin" / "alvis"}" "$@"\n'
    )
    wrapper.chmod(0o755)
    return wrapper


def _persist_metadata(settings: Settings, *, version: str, tarball_url: str) -> None:
    install_metadata_path(settings).write_text(
        json.dumps(
            {
                "version": version,
                "release_repo": settings.release_repo,
                "tarball_url": tarball_url,
                "installed_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )


def _verify_daemon_version(settings: Settings, target_version: str) -> dict:
    client = DaemonClient(settings)
    try:
        health = client.health(settings.repo_root)
    except DaemonUnavailableError as exc:
        return {
            "daemon_restarted": False,
            "daemon_version": None,
            "daemon_version_matches_target": False,
            "daemon_error": str(exc),
        }
    daemon_version = health.get("version")
    return {
        "daemon_restarted": health.get("status") == "ok",
        "daemon_version": daemon_version,
        "daemon_version_matches_target": _normalize_version(daemon_version) == _normalize_version(target_version),
        "daemon_error": None if _normalize_version(daemon_version) == _normalize_version(target_version) else "daemon version mismatch",
    }


def _daemon_result(settings: Settings, target_version: str, *, restart: bool) -> dict:
    daemon_result = {
        "daemon_restarted": False,
        "daemon_version": None,
        "daemon_version_matches_target": None,
        "daemon_error": None,
    }
    if not shutil.which("launchctl"):
        return daemon_result
    if restart:
        manager = LaunchdManager(settings)
        manager.stop()
        manager.start()
        daemon_result["daemon_restarted"] = True
    daemon_result = {**daemon_result, **_verify_daemon_version(settings, target_version)}
    return daemon_result


def _install_from_source(
    settings: Settings,
    source_dir: Path,
    *,
    version: str,
    tarball_url: str,
    codex_command: str | None = None,
) -> None:
    app_dir = install_app_dir(settings)
    if app_dir.exists():
        shutil.rmtree(app_dir)
    shutil.copytree(source_dir, app_dir)
    venv_dir = install_venv_dir(settings)
    if not venv_dir.exists():
        subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
    subprocess.run([str(venv_dir / "bin" / "python"), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(venv_dir / "bin" / "python"), "-m", "pip", "install", str(app_dir)], check=True)
    _write_wrapper(settings, codex_command=codex_command)
    _persist_metadata(settings, version=version, tarball_url=tarball_url)


def _build_result(
    *,
    status: str,
    current_version: str | None,
    target_version: str,
    install_state: dict,
    metadata_updated: bool,
    daemon_result: dict,
) -> dict:
    metadata_version = install_state.get("metadata_version")
    installed_app_version = install_state.get("installed_app_version")
    return {
        "status": status,
        "current_version": current_version,
        "target_version": target_version,
        "metadata_version": metadata_version,
        "installed_app_version": installed_app_version,
        "install_drift_detected": _normalize_version(metadata_version) != _normalize_version(installed_app_version),
        "metadata_updated": metadata_updated,
        **daemon_result,
    }


def perform_upgrade(settings: Settings, version: str | None = None) -> dict:
    ensure_runtime_dirs(settings)
    release = _fetch_release(settings, version)
    tag = release["tag_name"]
    tarball_url = release["tarball_url"]
    state = inspect_installation_state(settings)
    current = state.get("installed_app_version") or state.get("metadata_version") or __version__
    daemon_result = _daemon_result(settings, tag, restart=False)

    installed_matches = _normalize_version(state.get("installed_app_version")) == _normalize_version(tag)
    wrapper_ready = state.get("wrapper_exists", False)
    venv_ready = state.get("venv_entrypoint_exists", False)
    daemon_matches = daemon_result.get("daemon_version_matches_target") is True or daemon_result.get("daemon_version_matches_target") is None and not shutil.which("launchctl")
    metadata_matches = _normalize_version(state.get("metadata_version")) == _normalize_version(tag)

    if installed_matches and wrapper_ready and venv_ready and daemon_matches:
        metadata_updated = False
        if not metadata_matches:
            _persist_metadata(settings, version=tag, tarball_url=tarball_url)
            metadata_updated = True
            state = inspect_installation_state(settings)
        return _build_result(
            status="noop",
            current_version=current,
            target_version=tag,
            install_state=state,
            metadata_updated=metadata_updated,
            daemon_result=daemon_result,
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        tarball = temp_path / "release.tar.gz"
        _download(tarball_url, tarball)
        source_dir = temp_path / "src"
        source_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball, "r:gz") as archive:
            archive.extractall(source_dir)
        extracted = next(source_dir.iterdir())
        _install_from_source(settings, extracted, version=tag, tarball_url=tarball_url)

    daemon_result = _daemon_result(settings, tag, restart=True)
    state = inspect_installation_state(settings)
    if daemon_result.get("daemon_version_matches_target") is False:
        return _build_result(
            status="daemon_mismatch",
            current_version=current,
            target_version=tag,
            install_state=state,
            metadata_updated=False,
            daemon_result=daemon_result,
        )
    return _build_result(
        status="upgraded",
        current_version=current,
        target_version=tag,
        install_state=state,
        metadata_updated=False,
        daemon_result=daemon_result,
    )


def install_from_release(settings: Settings, version: str | None = None) -> dict:
    install_root(settings).mkdir(parents=True, exist_ok=True)
    return perform_upgrade(settings, version)


def install_from_source(
    settings: Settings,
    source_dir: Path,
    *,
    version: str,
    tarball_url: str,
    codex_command: str | None = None,
) -> dict:
    install_root(settings).mkdir(parents=True, exist_ok=True)
    ensure_runtime_dirs(settings)
    _install_from_source(settings, source_dir, version=version, tarball_url=tarball_url, codex_command=codex_command)
    daemon_result = _daemon_result(settings, version, restart=True)
    state = inspect_installation_state(settings)
    status = "installed"
    if daemon_result.get("daemon_version_matches_target") is False:
        status = "daemon_mismatch"
    return _build_result(
        status=status,
        current_version=state.get("installed_app_version") or state.get("metadata_version") or __version__,
        target_version=version,
        install_state=state,
        metadata_updated=False,
        daemon_result=daemon_result,
    )
