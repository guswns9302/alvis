from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.server as server_module
from app.api.server import create_app


class FakeServices:
    def daemon_health(self) -> dict:
        return {
            "status": "ok",
            "codex_command": "/usr/local/bin/codex",
            "workspace_root": "/tmp/workspace",
            "data_dir": "/tmp/data",
            "db_path": "/tmp/data/alvis.db",
            "runtime_dir": "/tmp/data/runtime",
            "team_count": 2,
        }

    def start_or_attach_default_team(self) -> dict:
        return {"action": "created", "team_id": "team-demo", "session_name": None}

    def clean_workspace_teams(self) -> dict:
        return {"removed_teams": [], "skipped_teams": [], "removed_count": 0, "skipped_count": 0}


def test_health_reports_daemon_runtime(monkeypatch):
    monkeypatch.setattr(server_module, "bootstrap_services", lambda workspace_root=None: FakeServices())
    client = TestClient(create_app())

    response = client.get("/health", params={"workspace_root": "/tmp/demo"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["daemon_codex_command"] == "/usr/local/bin/codex"
    assert payload["daemon_workspace_root"] == "/tmp/workspace"
    assert payload["daemon_db_path"] == "/tmp/data/alvis.db"
    assert payload["daemon_team_count"] == 2


def test_start_workspace_returns_created_payload(monkeypatch):
    monkeypatch.setattr(server_module, "bootstrap_services", lambda workspace_root=None: FakeServices())
    client = TestClient(create_app())

    response = client.post("/start", json={"workspace_root": "/tmp/demo"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "created"
    assert payload["team_id"] == "team-demo"
