from __future__ import annotations

import json
import re
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


def install_node_runtime_dir(settings: Settings) -> Path:
    return settings.app_home / "node-runtime"


def install_node_worker_path(settings: Settings) -> Path:
    return install_node_runtime_dir(settings) / "worker.mjs"


def install_node_package_path(settings: Settings) -> Path:
    return install_node_runtime_dir(settings) / "package.json"


def install_node_modules_dir(settings: Settings) -> Path:
    return install_node_runtime_dir(settings) / "node_modules"


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


def read_installed_app_version(settings: Settings) -> str | None:
    version_file = install_app_dir(settings) / "app" / "version.py"
    if not version_file.exists():
        return None
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version_file.read_text())
    if not match:
        return None
    return match.group(1)


def install_venv_entrypoint_path(settings: Settings) -> Path:
    return install_venv_dir(settings) / "bin" / "alvis"


def inspect_installation_state(settings: Settings) -> dict:
    metadata = read_install_metadata(settings)
    app_dir = install_app_dir(settings)
    wrapper = install_wrapper_path(settings)
    venv_entrypoint = install_venv_entrypoint_path(settings)
    node_runtime = install_node_runtime_dir(settings)
    node_worker = install_node_worker_path(settings)
    node_package = install_node_package_path(settings)
    node_modules = install_node_modules_dir(settings)
    codex_sdk_package = node_modules / "@openai" / "codex-sdk" / "package.json"
    return {
        "metadata_version": metadata.get("version"),
        "installed_app_version": read_installed_app_version(settings),
        "app_dir_exists": app_dir.exists(),
        "wrapper_exists": wrapper.exists(),
        "venv_entrypoint_exists": venv_entrypoint.exists(),
        "node_runtime_dir_exists": node_runtime.exists(),
        "node_worker_exists": node_worker.exists(),
        "node_package_exists": node_package.exists(),
        "codex_sdk_package_installed": codex_sdk_package.exists(),
        "app_dir": str(app_dir),
        "wrapper_path": str(wrapper),
        "venv_entrypoint_path": str(venv_entrypoint),
        "node_runtime_dir": str(node_runtime),
        "node_worker_path": str(node_worker),
        "node_package_path": str(node_package),
    }
