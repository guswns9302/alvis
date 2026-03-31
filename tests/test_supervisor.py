from uuid import uuid4

from pathlib import Path

import pytest

from app.config import Settings, ensure_runtime_dirs
from app.db.base import create_session_factory, session_scope
from app.db.init_db import init_db
from app.db.models import RunModel
from app.db.repository import Repository
from app.enums import EventType, RunStatus, TaskStatus
from app.graph.supervisor import Supervisor, SupervisorDeps
from app.services import AlvisServices


REPO_ROOT = Path(__file__).resolve().parents[1]


def create_services(tmp_path: Path) -> AlvisServices:
    repo_root = tmp_path / "project"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "README.md").write_text("shared root")
    fake_codex = REPO_ROOT / "tests" / "fixtures" / "fake_codex_session.py"
    settings = Settings(
        repo_root=repo_root,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "alvis.db",
        log_dir=tmp_path / "data" / "logs",
        runtime_dir=tmp_path / "data" / "runtime",
        worktree_root=tmp_path / "runtime-cache",
        codex_command=f"{REPO_ROOT / '.venv' / 'bin' / 'python'} {fake_codex}",
    )
    ensure_runtime_dirs(settings)
    init_db(settings)
    return AlvisServices(settings=settings, session_factory=create_session_factory(settings))


def test_supervisor_creates_run_and_tasks(tmp_path):
    services = create_services(tmp_path)
    team_id = f"test-team-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")

    state = Supervisor(SupervisorDeps(services=services)).run(team_id, "fix a bug")

    assert state["run_id"].startswith("run-")
    assert len(services.list_run_tasks(state["run_id"])) >= 2


def test_supervisor_creates_reviewer_handoff_and_final_output(tmp_path):
    services = create_services(tmp_path)
    team_id = f"handoff-team-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")

    state = Supervisor(SupervisorDeps(services=services)).run(team_id, "fix a bug")
    tasks = services.list_run_tasks(state["run_id"])
    handoff = next(task for task in tasks if task.parent_task_id)
    events = services.list_events(team_id=team_id, run_id=state["run_id"])

    assert state["status"] == RunStatus.DONE.value
    assert handoff.title == "Validate and summarize"
    assert handoff.status == TaskStatus.DONE.value
    assert state["final_output_candidate"] is not None
    assert state["final_output_candidate"]["task_id"] == handoff.task_id
    assert state["final_output_ready"] is True
    assert any(event.event_type == EventType.TASK_HANDOFF_CREATED.value for event in events)
    assert any(event.event_type == EventType.LEADER_OUTPUT_READY.value for event in events)


def test_supervisor_creates_redo_when_worker_output_is_invalid(tmp_path):
    services = create_services(tmp_path)
    team_id = f"redo-team-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    supervisor = Supervisor(SupervisorDeps(services=services))

    original_dispatch = services.dispatch_task

    def invalid_dispatch(agent_id, contract):
        if "Redo:" in contract.goal or contract.goal.startswith("Redo the original task"):
            return original_dispatch(agent_id, contract)
        services.append_event(
            team_id=team_id,
            run_id=contract.context["run_id"],
            task_id=contract.task_id,
            agent_id=agent_id,
            event_type=EventType.AGENT_OUTPUT_FINAL.value,
            payload={
                "task_id": contract.task_id,
                "agent_id": agent_id,
                "kind": "final",
                "status_signal": "blocked",
                "summary": "Task did not produce a valid structured result block.",
                "question_for_leader": [],
                "requested_context": [],
                "followup_suggestion": [],
                "dependency_note": [],
                "changed_files": [],
                "test_results": [],
                "risk_flags": ["Missing ALVIS structured result block."],
            },
        )
        return type("Dispatch", (), {"ok": True, "reason": "background_exec", "prompt": "invalid"})()

    services.dispatch_task = invalid_dispatch  # type: ignore[method-assign]

    state = supervisor.run(team_id, "fix a bug")
    tasks = services.list_run_tasks(state["run_id"])
    redo_task = next(task for task in tasks if task.title.startswith("Redo:"))

    assert state["status"] == RunStatus.DONE.value
    assert state["final_output_ready"] is True
    assert redo_task.target_role_alias == "builder"
    assert state["final_output_candidate"]["task_id"] == redo_task.task_id


def test_supervisor_persists_checkpoint_for_active_run(tmp_path):
    services = create_services(tmp_path)
    team_id = f"checkpoint-team-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    supervisor = Supervisor(SupervisorDeps(services=services))
    captured: dict[str, str] = {}

    def stop_after_dispatch(state):
        captured["run_id"] = state["run_id"]
        raise RuntimeError("stop after dispatch")

    supervisor.wait_for_updates = stop_after_dispatch  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="stop after dispatch"):
        supervisor.run(team_id, "long running work")

    checkpoint = services.load_checkpoint(captured["run_id"])

    assert checkpoint is not None
    assert checkpoint.thread_id == captured["run_id"]
    assert checkpoint.next_node == "wait_for_updates"


def test_recover_blocks_missing_pane_and_orphaned_task(tmp_path):
    services = create_services(tmp_path)
    team_id = f"recover-team-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    state = Supervisor(SupervisorDeps(services=services)).run(team_id, "fix a bug")
    target_task = next(task["task_id"] for task in state["tasks"] if not task.get("parent_task_id"))

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(f"{team_id}-worker-1")
        task = session.get(type(services.get_task(target_task)), target_task)
        repo.update_agent(agent, tmux_pane="%999", current_task_id=target_task, status="running")
        repo.update_task(task, status=TaskStatus.RUNNING.value)

    services.tmux.pane_exists = lambda pane_id: False  # type: ignore[method-assign]
    report = services.recover(team_id=team_id)

    assert f"{team_id}-worker-1" in report["missing_panes"]
    assert target_task in report["orphaned_tasks"]
    assert any(action["type"] == "task_blocked_orphaned" for action in report["actions_taken"])
    assert services.get_task(target_task).status == TaskStatus.BLOCKED.value


def test_recover_reconciles_dangling_run_to_done(tmp_path):
    services = create_services(tmp_path)
    team_id = f"dangling-team-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    run = services.create_run(team_id, "noop")

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        db_run = session.get(RunModel, run.run_id)
        repo.mark_run_status(db_run, RunStatus.RUNNING, "Still running")

    report = services.recover(team_id=team_id)

    assert run.run_id in report["dangling_runs"]
    assert run.run_id in report["reconciled_runs"]
    assert services.get_run(run.run_id).status == RunStatus.DONE.value


def test_dispatch_conflict_blocks_before_assignment(tmp_path):
    settings = Settings(
        repo_root=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "alvis.db",
        log_dir=tmp_path / "data" / "logs",
        runtime_dir=tmp_path / "data" / "runtime",
        worktree_root=tmp_path / "worktrees",
        codex_command="sh",
    )
    ensure_runtime_dirs(settings)
    init_db(settings)
    services = AlvisServices(settings=settings, session_factory=create_session_factory(settings))
    team_id = f"conflict-dispatch-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")

    supervisor = Supervisor(SupervisorDeps(services=services))
    supervisor.create_plan = lambda request, workers: [  # type: ignore[method-assign]
        {
            "title": "Implement changes",
            "goal": request,
            "target_role_alias": "builder",
            "owned_paths": ["shared.py"],
            "review_required": False,
        }
    ]
    services.inspect_worktrees = lambda team: {  # type: ignore[method-assign]
        "workspaces": [],
        "cleanup_candidates": [],
        "scope_conflicts": [{"paths": ["shared.py"], "owners": [{"agent_id": f"{team_id}-worker-1", "task_id": "existing-task", "path": "/tmp"}]}],
    }

    state = supervisor.run(team_id, "conflict run")
    created_task_id = state["tasks"][0]["task_id"]
    created_task = services.get_task(created_task_id)
    events = services.list_events(team_id=team_id, run_id=state["run_id"])

    assert created_task.status == TaskStatus.BLOCKED.value
    assert created_task.agent_id is None
    assert not any(event.event_type == EventType.TASK_ASSIGNED.value and event.task_id == created_task_id for event in events)


def test_worker_question_routes_to_leader_queue(tmp_path):
    services = create_services(tmp_path)
    team_id = f"leader-route-{uuid4().hex[:8]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    supervisor = Supervisor(SupervisorDeps(services=services))
    run = services.create_run(team_id, "need clarification")
    task = services.create_task(
        team_id,
        run.run_id,
        "Implement changes",
        "Implement the requested work for: need clarification",
        target_role_alias="builder",
        owned_paths=["README.md"],
    )
    services.assign_task(task.task_id, f"{team_id}-worker-1")
    interaction = services.create_interaction(
        run_id=run.run_id,
        team_id=team_id,
        kind="request_input",
        payload={"message": "Which section should be updated first?"},
        source_agent_id=f"{team_id}-worker-1",
        target_role_alias="leader",
        task_id=task.task_id,
    )

    routed = supervisor.route_interactions(
        {
            "team_id": team_id,
            "run_id": run.run_id,
            "tasks": [{"task_id": task.task_id}],
            "assignments": [],
            "active_tasks": [],
            "completed_tasks": [],
            "blocked_tasks": [],
            "review_requests": [],
            "pending_interactions": [{"interaction_id": interaction.interaction_id}],
            "handoffs": [],
            "final_output_candidate": None,
            "status": RunStatus.RUNNING.value,
        }
    )
    assert routed["pending_interactions"]
    assert any(item.status == "pending" for item in services.list_interactions(run_id=run.run_id))
