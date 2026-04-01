from __future__ import annotations

from types import SimpleNamespace

from app.runtime import leader_console, worker_dashboard


class StubServices:
    def __init__(self):
        self.codex = SimpleNamespace(session_paths=lambda agent_id: {})
        self.settings = SimpleNamespace(repo_root="/tmp/repo")

    def status(self, team_id: str) -> dict:
        return {
            "team_id": team_id,
            "agents": [
                {
                    "agent_id": f"{team_id}-leader",
                    "role": "leader",
                    "role_alias": "leader",
                    "status": "idle",
                    "task": None,
                    "runtime_health": {"status": "ready"},
                },
                {
                    "agent_id": f"{team_id}-worker-1",
                    "role": "reviewer",
                    "role_alias": "checker",
                    "status": "running",
                    "task": "task-1",
                    "runtime_health": {"status": "ready"},
                },
                {
                    "agent_id": f"{team_id}-worker-2",
                    "role": "analyst",
                    "role_alias": "analyst",
                    "status": "idle",
                    "task": None,
                    "runtime_health": {"status": "ready"},
                },
            ],
            "latest_run": {
                "run_id": "run-1",
                "status": "running",
                "request": "hdmi 조사",
                "final_response": "최종 정리",
            },
            "tasks": [
                {
                    "task_id": "task-1",
                    "title": "Validate and summarize",
                    "goal": "검토",
                    "latest_output": {"summary": "검토 중", "status_signal": "needs_review"},
                }
            ],
            "handoffs": [{"task_id": "task-1"}],
            "redo_tasks": [],
            "final_output_candidate": {"summary": "후보 결과"},
            "final_output_ready": True,
        }

    def list_events(self, team_id: str):
        return [
            SimpleNamespace(
                event_id="evt-1",
                event_type="task.assigned",
                agent_id=f"{team_id}-worker-1",
                payload={"summary": "Task assigned"},
            ),
            SimpleNamespace(
                event_id="evt-2",
                event_type="leader.output.ready",
                agent_id=f"{team_id}-worker-1",
                payload={"summary": "Leader output ready"},
            ),
        ]


def test_leader_render_includes_worker_status_and_final_result(monkeypatch):
    stub = StubServices()
    monkeypatch.setattr(leader_console, "bootstrap_services", lambda: stub)
    monkeypatch.setattr(leader_console, "_write_ready_state", lambda team_id: None)

    rendered = leader_console._render_buffer("demo", "", True)

    assert "워커 상태:" in rendered
    assert "작업 로그:" in rendered
    assert "최종 결과:" in rendered
    assert "최종 정리" in rendered


def test_worker_dashboard_is_log_only(monkeypatch):
    stub = StubServices()
    monkeypatch.setattr(worker_dashboard, "bootstrap_services", lambda: stub)

    events = worker_dashboard.filtered_events(stub, "demo", worker_dashboard.WORKER_LOG_EVENTS)

    assert [worker["role_alias"] for worker in worker_dashboard.worker_agents(stub.status("demo"))] == ["checker", "analyst"]
    assert [worker_dashboard.format_timeline_entry(event, stub.status("demo")) for event in events] == [
        "[1/checker] Task assigned",
        "[1/checker] Leader output ready",
    ]
