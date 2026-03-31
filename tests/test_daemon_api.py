from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.server as server_module
from app.api.server import create_app
from app.sessions.tmux_manager import TmuxUnavailableError


class FakeServices:
    def __init__(self, *, provision_error: Exception | None = None):
        self.provision_error = provision_error

    def daemon_health(self) -> dict:
        return {
            "status": "ok",
            "tmux_path": "/opt/homebrew/bin/tmux",
            "tmux_available": True,
            "codex_command": "/usr/local/bin/codex",
            "workspace_root": "/tmp/workspace",
            "data_dir": "/tmp/data",
        }

    def provision_team(self, team_id: str, worker_1_role: str, worker_2_role: str) -> dict:
        if self.provision_error:
            raise self.provision_error
        return {
            "team": type("Team", (), {"team_id": team_id})(),
            "start_result": {"team_id": team_id, "all_ready": True},
        }


def test_health_reports_daemon_runtime(monkeypatch):
    monkeypatch.setattr(server_module, "bootstrap_services", lambda workspace_root=None: FakeServices())
    client = TestClient(create_app())

    response = client.get("/health", params={"workspace_root": "/tmp/demo"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["daemon_tmux_path"] == "/opt/homebrew/bin/tmux"
    assert payload["daemon_tmux_available"] is True
    assert payload["daemon_codex_command"] == "/usr/local/bin/codex"


def test_create_team_returns_conflict_for_existing_team(monkeypatch):
    monkeypatch.setattr(
        server_module,
        "bootstrap_services",
        lambda workspace_root=None: FakeServices(
            provision_error=ValueError("team demo already exists; use `alvis team remove demo` first or choose a new name")
        ),
    )
    client = TestClient(create_app())

    response = client.post(
        "/teams/create",
        json={
            "workspace_root": "/tmp/demo",
            "team_id": "demo",
            "worker_1_role": "reviewer:reviewer",
            "worker_2_role": "analyst:analyst",
        },
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "team_exists"
    assert "already exists" in payload["detail"]


def test_create_team_returns_tmux_unavailable(monkeypatch):
    monkeypatch.setattr(
        server_module,
        "bootstrap_services",
        lambda workspace_root=None: FakeServices(
            provision_error=TmuxUnavailableError("tmux is not installed or not available on PATH")
        ),
    )
    client = TestClient(create_app())

    response = client.post(
        "/teams/create",
        json={
            "workspace_root": "/tmp/demo",
            "team_id": "demo",
            "worker_1_role": "reviewer:reviewer",
            "worker_2_role": "analyst:analyst",
        },
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["error_code"] == "tmux_unavailable"
    assert "tmux" in payload["detail"]
