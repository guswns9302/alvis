from __future__ import annotations

import json
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
    install_metadata_path,
    install_root,
    install_venv_dir,
    install_wrapper_path,
    read_install_metadata,
)
from app.launchd import LaunchdManager
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


def _write_wrapper(settings: Settings) -> Path:
    bin_dir = install_bin_dir(settings)
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = install_wrapper_path(settings)
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'set -euo pipefail\n'
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


def perform_upgrade(settings: Settings, version: str | None = None) -> dict:
    ensure_runtime_dirs(settings)
    release = _fetch_release(settings, version)
    tag = release["tag_name"]
    tarball_url = release["tarball_url"]
    current = read_install_metadata(settings).get("version", __version__)
    if current == tag:
        return {"status": "noop", "current_version": current, "target_version": tag}

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        tarball = temp_path / "release.tar.gz"
        _download(tarball_url, tarball)
        source_dir = temp_path / "src"
        source_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball, "r:gz") as archive:
            archive.extractall(source_dir)
        extracted = next(source_dir.iterdir())
        app_dir = install_app_dir(settings)
        if app_dir.exists():
            shutil.rmtree(app_dir)
        shutil.copytree(extracted, app_dir)
        venv_dir = install_venv_dir(settings)
        if not venv_dir.exists():
            subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
        subprocess.run([str(venv_dir / "bin" / "python"), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        subprocess.run([str(venv_dir / "bin" / "python"), "-m", "pip", "install", str(app_dir)], check=True)
        _write_wrapper(settings)
        _persist_metadata(settings, version=tag, tarball_url=tarball_url)

    if shutil.which("launchctl"):
        LaunchdManager(settings).restart()
    return {"status": "upgraded", "current_version": current, "target_version": tag}


def install_from_release(settings: Settings, version: str | None = None) -> dict:
    install_root(settings).mkdir(parents=True, exist_ok=True)
    return perform_upgrade(settings, version)
