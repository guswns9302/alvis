import time
from uuid import uuid4

import pytest

from app.config import Settings, ensure_runtime_dirs
from app.db.base import create_session_factory, session_scope
from app.db.init_db import init_db
from app.db.models import AgentModel, TaskModel
from app.db.repository import Repository
from app.enums import TaskStatus
from app.services import AlvisServices
from app.sessions.tmux_manager import TmuxManager


def create_test_services(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        repo_root=repo_root,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "alvis.db",
        log_dir=tmp_path / "data" / "logs",
        runtime_dir=tmp_path / "data" / "runtime",
        worktree_root=tmp_path / "worktrees",
        tmux_session_prefix=f"alvis-test-{uuid4().hex[:6]}",
        codex_command="sh",
    )
    ensure_runtime_dirs(settings)
    init_db(settings)
    session_factory = create_session_factory(settings)
    return AlvisServices(settings=settings, session_factory=session_factory)


def wait_for(predicate, timeout=3.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_tmux_manager_creates_expected_panes(tmp_path):
    services = create_test_services(tmp_path)
    tmux = services.tmux
    team_id = f"tmux-team-{uuid4().hex[:6]}"
    session_name = tmux.create_team_layout(team_id, 2)
    try:
        panes = tmux.list_panes(session_name)
        assert len(panes) == 2
    finally:
        tmux.kill_session(session_name)


def test_tmux_send_input_and_capture_roundtrip(tmp_path):
    services = create_test_services(tmp_path)
    tmux = services.tmux
    team_id = f"tmux-send-{uuid4().hex[:6]}"
    session_name = tmux.create_team_layout(team_id, 1)
    try:
        pane_id = tmux.list_panes(session_name)[0]
        tmux.send_input(pane_id, "printf 'hello-from-pane\\n'")
        assert wait_for(lambda: "hello-from-pane" in tmux.capture_debug_snapshot(pane_id, lines=50))
    finally:
        tmux.kill_session(session_name)


def test_pipe_pane_writes_to_log_file(tmp_path):
    services = create_test_services(tmp_path)
    tmux = services.tmux
    team_id = f"tmux-log-{uuid4().hex[:6]}"
    session_name = tmux.create_team_layout(team_id, 1)
    log_path = tmp_path / "pane.log"
    try:
        pane_id = tmux.list_panes(session_name)[0]
        tmux.pipe_pane_to_file(pane_id, log_path)
        tmux.send_input(pane_id, "printf 'log-line-from-pane\\n'")
        assert wait_for(lambda: log_path.exists() and "log-line-from-pane" in log_path.read_text())
    finally:
        tmux.kill_session(session_name)


def test_recover_marks_missing_pane_with_real_tmux(tmp_path):
    services = create_test_services(tmp_path)
    team_id = f"recover-real-{uuid4().hex[:6]}"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    session_name = services.tmux.create_team_layout(team_id, 2)
    panes = services.tmux.list_panes(session_name)
    agent_id = f"{team_id}-worker-1"
    task_id = f"task-real-{uuid4().hex[:6]}"
    run = services.create_run(team_id, "real tmux recovery")

    try:
        with session_scope(services.session_factory) as session:
            repo = Repository(session)
            agent = repo.get_agent(agent_id)
            repo.update_agent(
                agent,
                tmux_session=session_name,
                tmux_pane=panes[-1],
                current_task_id=task_id,
                status="running",
            )
            task = TaskModel(
                task_id=task_id,
                team_id=team_id,
                run_id=run.run_id,
                title="runtime task",
                goal="exercise recover",
                status=TaskStatus.RUNNING.value,
                agent_id=agent_id,
                target_role_alias="builder",
                owned_paths=["README.md"],
            )
            session.add(task)
            session.flush()

        services.tmux.kill_session(session_name)
        report = services.recover(team_id=team_id)
        assert agent_id in report["missing_panes"]
        assert task_id in report["orphaned_tasks"]
        assert any(action["type"] == "task_blocked_orphaned" for action in report["actions_taken"])
        assert services.get_task(task_id).status == TaskStatus.BLOCKED.value
    finally:
        services.tmux.kill_session(session_name)


def test_provision_team_rolls_back_partial_create(tmp_path, monkeypatch):
    services = create_test_services(tmp_path)
    team_id = f"tmux-provision-{uuid4().hex[:6]}"

    def fail_start(_: str):
        raise RuntimeError("synthetic start failure")

    monkeypatch.setattr(services, "start_team", fail_start)

    with pytest.raises(RuntimeError, match="synthetic start failure"):
        services.provision_team(team_id, "implementer:builder", "reviewer:checker")

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        assert repo.get_team(team_id) is None
        assert repo.list_agents(team_id) == []


def test_start_team_does_not_block_on_each_worker_runtime(tmp_path, monkeypatch):
    services = create_test_services(tmp_path)
    team_id = f"tmux-dashboard-{uuid4().hex[:6]}"
    services.create_team(team_id, "reviewer:checker", "analyst:analyst")

    monkeypatch.setattr(services.tmux, "create_team_layout", lambda team_id, pane_count, commands: "session-demo")
    monkeypatch.setattr(services.tmux, "list_panes", lambda session_name: ["%1", "%2"])

    bootstrap_calls: list[str] = []

    def record_bootstrap(agent_id: str, pane_id: str, cwd: str):
        bootstrap_calls.append(agent_id)
        return {"state": "ok"}

    monkeypatch.setattr(services.codex, "bootstrap_session", record_bootstrap)
    monkeypatch.setattr(services, "runtime_health", lambda agent: {"status": "ready", "ready": True})

    result = services.start_team(team_id)

    assert bootstrap_calls == [f"{team_id}-leader"]
    assert result["all_ready"] is True
    assert sorted(result["ready_agents"]) == [
        f"{team_id}-leader",
        f"{team_id}-worker-1",
        f"{team_id}-worker-2",
    ]
