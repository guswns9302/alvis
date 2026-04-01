from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import uuid4

from app.agents.codex_adapter import CodexAdapter
from app.config import Settings, ensure_runtime_dirs
from app.db.base import create_session_factory, session_scope
from app.db.init_db import init_db
from app.db.models import TaskModel
from app.db.repository import Repository
from app.enums import AgentStatus, EventType, TaskStatus
from app.runtime.noninteractive_task_runner import _build_invocation
from app.schemas import DispatchResult
from app.services import AlvisServices

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_task_runner_build_invocation_normalizes_bare_codex(tmp_path):
    schema = tmp_path / "schema.json"
    output = tmp_path / "output.json"

    invocation = _build_invocation("codex", schema, output, tmp_path / "last.txt")

    assert invocation[:4] == ["codex", "exec", "--color", "never"]
    assert "--output-schema" in invocation
    assert "--skip-git-repo-check" in invocation


def create_services(tmp_path: Path) -> AlvisServices:
    repo_root = tmp_path / "project"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "README.md").write_text("shared root")
    settings = Settings(
        repo_root=repo_root,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "alvis.db",
        log_dir=tmp_path / "data" / "logs",
        runtime_dir=tmp_path / "data" / "runtime",
        worktree_root=tmp_path / "runtime-cache",
        tmux_session_prefix=f"alvis-test-{uuid4().hex[:6]}",
        codex_command="sh",
    )
    ensure_runtime_dirs(settings)
    init_db(settings)
    session_factory = create_session_factory(settings)
    return AlvisServices(settings=settings, session_factory=session_factory)


def create_fake_runtime_services(tmp_path: Path) -> AlvisServices:
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
        tmux_session_prefix=f"alvis-test-{uuid4().hex[:6]}",
        codex_command=f"{REPO_ROOT / '.venv' / 'bin' / 'python'} {fake_codex}",
    )
    ensure_runtime_dirs(settings)
    init_db(settings)
    return AlvisServices(settings=settings, session_factory=create_session_factory(settings))


def test_cleanup_removes_orphaned_runtime_dir(tmp_path):
    services = create_services(tmp_path)
    team_id = f"cleanup-team-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    runtime_dir = services.codex.session_paths(agent_id)["dir"]
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "pane.log").write_text("stale output")

    report = services.cleanup_worktrees(team_id=team_id)

    assert any(item["agent_id"] == agent_id for item in report["deleted_runtime_dirs"])
    assert not runtime_dir.exists()


def test_inspect_workspaces_detects_scope_conflicts(tmp_path):
    services = create_services(tmp_path)
    team_id = f"conflict-team-{uuid4().hex[:6]}"
    services.create_team(team_id, "implementer:backend", "implementer:test")
    run = services.create_run(team_id, "update shared.py")

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        worker_1 = repo.get_agent(f"{team_id}-worker-1")
        worker_2 = repo.get_agent(f"{team_id}-worker-2")
        task_1 = repo.create_task(team_id, run.run_id, "Backend", "edit shared.py", target_role_alias="backend", owned_paths=["shared.py"])
        task_2 = repo.create_task(team_id, run.run_id, "Tests", "edit shared.py", target_role_alias="test", owned_paths=["shared.py"])
        repo.update_agent(worker_1, status=AgentStatus.RUNNING.value, current_task_id=task_1.task_id)
        repo.update_agent(worker_2, status=AgentStatus.RUNNING.value, current_task_id=task_2.task_id)
        repo.update_task(task_1, status=TaskStatus.RUNNING.value, agent_id=worker_1.agent_id)
        repo.update_task(task_2, status=TaskStatus.RUNNING.value, agent_id=worker_2.agent_id)

    report = services.inspect_worktrees(team_id)

    assert len(report["scope_conflicts"]) == 1
    assert report["scope_conflicts"][0]["paths"] == ["shared.py"]


def test_recover_retry_re_dispatches_blocked_task_on_same_agent(tmp_path):
    services = create_services(tmp_path)
    team_id = f"retry-team-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    run = services.create_run(team_id, "retry blocked task")

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(
            agent,
            cwd=str(services.settings.repo_root),
            status=AgentStatus.BLOCKED.value,
            current_task_id="task-retry-1",
        )
        task = TaskModel(
            task_id="task-retry-1",
            team_id=team_id,
            run_id=run.run_id,
            title="retry task",
            goal="retry this task",
            status=TaskStatus.BLOCKED.value,
            agent_id=agent_id,
            target_role_alias="builder",
            owned_paths=["src/app.py"],
        )
        session.add(task)
        session.flush()

    services.codex.runtime_health = lambda agent_id, pane_exists: {"status": "ready", "ready": True}  # type: ignore[method-assign]
    services.dispatch_task = lambda agent_id, contract: DispatchResult(ok=True, prompt="RETRY_PROMPT")  # type: ignore[method-assign]
    report = services.recover(team_id=team_id, retry=True)

    assert any(action["type"] == "retry_attempted" for action in report["actions_taken"])
    assert services.get_task("task-retry-1").status == TaskStatus.RUNNING.value
    events = services.list_events(team_id=team_id, run_id=run.run_id)
    assert any(event.event_type == EventType.TASK_RETRY_REQUESTED.value for event in events)


def test_retry_candidate_status_matches_recover_threshold_rules(tmp_path):
    services = create_services(tmp_path)
    team_id = f"retry-status-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    run = services.create_run(team_id, "retry candidate status")

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(
            agent,
            cwd=str(services.settings.repo_root),
            status=AgentStatus.BLOCKED.value,
            current_task_id="task-retry-status-1",
        )
        task = TaskModel(
            task_id="task-retry-status-1",
            team_id=team_id,
            run_id=run.run_id,
            title="retry status task",
            goal="retry candidate status task",
            status=TaskStatus.BLOCKED.value,
            agent_id=agent_id,
            target_role_alias="builder",
            owned_paths=["src/app.py"],
        )
        session.add(task)
        session.flush()
        for count in range(services.settings.review_retry_threshold):
            repo.append_event(
                team_id=team_id,
                run_id=run.run_id,
                task_id=task.task_id,
                agent_id=agent_id,
                event_type=EventType.TASK_RETRY_REQUESTED.value,
                payload={"summary": "Retry requested", "retry_count": count + 1},
            )

    services.codex.runtime_health = lambda agent_id, pane_exists: {"status": "ready", "ready": True}  # type: ignore[method-assign]
    status = services.status(team_id)
    report = services.recover(team_id=team_id, retry=True)

    assert status["retry_candidates"] == []
    assert any(action["type"] == "retry_skipped" and action["reason"] == "retry threshold exceeded" for action in report["actions_taken"])


def test_background_runner_success_does_not_emit_error_event(tmp_path):
    services = create_fake_runtime_services(tmp_path)
    team_id = f"background-ok-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    run = services.create_run(team_id, "fix a bug")
    task = services.create_task(team_id, run.run_id, "Implement", "fix a bug", target_role_alias="builder", owned_paths=["README.md"])
    services.assign_task(task.task_id, agent_id)
    agent = services.get_agent(agent_id)

    dispatch = services.dispatch_task(agent_id, services._default_task_contract(task, agent))  # type: ignore[attr-defined]
    deadline = time.time() + 5
    while time.time() < deadline:
        outputs = services.collect_outputs(team_id)
        if outputs:
            break
        time.sleep(0.1)
    events = services.list_events(team_id=team_id, run_id=run.run_id)

    assert dispatch.ok is True
    assert any(event.event_type == EventType.AGENT_OUTPUT_FINAL.value and event.task_id == task.task_id for event in events)
    assert not any(event.event_type == EventType.ERROR_RAISED.value and event.task_id == task.task_id for event in events)


def test_can_dispatch_blocks_when_session_not_ready(tmp_path):
    services = create_services(tmp_path)
    team_id = f"session-not-ready-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    run = services.create_run(team_id, "dispatch gate")
    task = services.create_task(team_id, run.run_id, "Implement", "Do the work", target_role_alias="builder", owned_paths=["src/app.py"])

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(agent, tmux_pane="%1")

    services.tmux.pane_exists = lambda pane_id: True  # type: ignore[method-assign]
    services.codex.runtime_health = lambda agent_id, pane_exists: {"status": "not_ready", "ready": False}  # type: ignore[method-assign]

    dispatch = services.can_dispatch_task(task.task_id, agent_id)

    assert dispatch.ok is False
    assert dispatch.reason == "not_ready"
    assert services.get_task(task.task_id).status == TaskStatus.BLOCKED.value


def test_recover_collects_uncollected_exited_output(tmp_path):
    services = create_fake_runtime_services(tmp_path)
    team_id = f"recover-output-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    run = services.create_run(team_id, "recover output")
    task = services.create_task(team_id, run.run_id, "Implement", "recover output", target_role_alias="builder", owned_paths=["README.md"])
    services.assign_task(task.task_id, agent_id)
    agent = services.get_agent(agent_id)

    dispatch = services.dispatch_task(agent_id, services.build_task_contract(task, agent))
    assert dispatch.ok is True

    state_path = services.codex.session_paths(agent_id)["state"]
    deadline = time.time() + 5
    while time.time() < deadline:
        state = services.codex.read_session_state(agent_id)
        if state.get("status") == "exited":
            state["output_collected"] = False
            state_path.write_text(json.dumps(state), encoding="utf-8")
            break
        time.sleep(0.1)

    report = services.recover(team_id=team_id)
    events = services.list_events(team_id=team_id, run_id=run.run_id)

    assert agent_id in report["collected_outputs"]
    assert any(event.event_type == EventType.AGENT_OUTPUT_FINAL.value and event.task_id == task.task_id for event in events)


def test_refresh_agent_runtime_surfaces_background_failure_reason(tmp_path):
    services = create_services(tmp_path)
    team_id = f"runtime-failure-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, "implementer:builder", "reviewer:checker")
    run = services.create_run(team_id, "diagnose failure")
    task = services.create_task(team_id, run.run_id, "Implement", "diagnose failure", target_role_alias="builder", owned_paths=["README.md"])
    services.assign_task(task.task_id, agent_id)
    paths = services.codex.session_paths(agent_id)
    paths["stderr"].write_text("error: unknown option '--output-schema'\n", encoding="utf-8")
    paths["state"].write_text(
        json.dumps(
            {
                "status": "exited",
                "pid": 1234,
                "exit_code": 1,
                "output_collected": False,
            }
        ),
        encoding="utf-8",
    )

    output = services.refresh_agent_runtime(agent_id)
    events = services.list_events(team_id=team_id, run_id=run.run_id)

    assert output is not None
    assert output.status_signal == "blocked"
    assert output.output_parse_status == "runtime_exec_failed"
    assert "Codex 실행 옵션이 현재 설치된 Codex 버전과 맞지 않아 실행에 실패했습니다." == output.summary
    assert any("다시 시도하세요" in item for item in output.risk_flags)
    assert any(
        event.event_type == EventType.ERROR_RAISED.value
        and event.payload.get("error_summary") == "Codex 실행 옵션이 현재 설치된 Codex 버전과 맞지 않아 실행에 실패했습니다."
        for event in events
    )
    assert services.list_interactions(run_id=run.run_id) == []


def test_codex_runtime_health_extracts_permission_error_summary(tmp_path):
    adapter = CodexAdapter(
        codex_command="codex",
        runtime_dir=tmp_path,
    )
    paths = adapter.session_paths("agent-1")
    paths["state"].write_text('{"status":"exited","exit_code":1}')
    paths["stderr"].write_text(
        "npm error code EACCES\n"
        "permission denied\n"
        "Error: `npm install -g @openai/codex` failed\n"
    )

    health = adapter.runtime_health("agent-1", pane_exists=True)

    assert health["status"] == "exited"
    assert "권한 오류(EACCES)" in health["error_summary"]
    assert "codex" in health["error_hint"]


def test_codex_runtime_health_extracts_stdin_terminal_error(tmp_path):
    adapter = CodexAdapter(
        codex_command="codex",
        runtime_dir=tmp_path,
    )
    paths = adapter.session_paths("agent-stdin")
    paths["state"].write_text('{"status":"exited","exit_code":1}')
    paths["stderr"].write_text("Error: stdin is not a terminal\n")

    health = adapter.runtime_health("agent-stdin", pane_exists=True)

    assert health["status"] == "exited"
    assert "stdin 계약" in health["error_summary"]
