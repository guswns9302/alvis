from __future__ import annotations

import json
from pathlib import Path


def credentials_path(app_home: Path) -> Path:
    return app_home / "credentials.json"


def load_saved_codex_api_key(app_home: Path) -> str | None:
    path = credentials_path(app_home)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    value = payload.get("codex_api_key")
    return value if isinstance(value, str) and value else None


def save_codex_api_key(app_home: Path, api_key: str) -> Path:
    path = credentials_path(app_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"codex_api_key": api_key}, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


def clear_saved_codex_api_key(app_home: Path) -> bool:
    path = credentials_path(app_home)
    if not path.exists():
        return False
    path.unlink()
    return True
