from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings


def install_root(settings: Settings) -> Path:
    return settings.app_home


def install_app_dir(settings: Settings) -> Path:
    return settings.app_home / "app"


def install_venv_dir(settings: Settings) -> Path:
    return settings.app_home / "venv"


def install_bin_dir(settings: Settings) -> Path:
    return settings.app_home / "bin"


def install_wrapper_path(settings: Settings) -> Path:
    return install_bin_dir(settings) / "alvis"


def install_metadata_path(settings: Settings) -> Path:
    return settings.app_home / "install.json"


def plist_path(settings: Settings) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{settings.launchd_label}.plist"


def daemon_log_path(settings: Settings) -> Path:
    return settings.app_home / "logs" / "daemon.log"


def daemon_error_log_path(settings: Settings) -> Path:
    return settings.app_home / "logs" / "daemon.stderr.log"


def read_install_metadata(settings: Settings) -> dict:
    path = install_metadata_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
