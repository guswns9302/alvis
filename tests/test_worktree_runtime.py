from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

from app.config import Settings, ensure_runtime_dirs
from app.db.base import create_session_factory, session_scope
from app.db.init_db import init_db
from app.db.models import TaskModel
from app.db.repository import Repository
from app.enums import AgentStatus, EventType, TaskStatus
from app.services import AlvisServices


REPO_ROOT = Path(__file__).resolve().parents[1]


def create_git_services(tmp_path: Path) -> AlvisServices:
    repo_root = tmp_path / "repo-clone"
    subprocess.run(["git", "clone", "--no-hardlinks", str(REPO_ROOT), str(repo_root)], check=True, capture_output=True, text=True)
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


def test_cleanup_removes_clean_orphaned_worktree(tmp_path):
    services = create_git_services(tmp_path)
    team_id = f"cleanup-team-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, 1)
    path, branch = services.worktrees.ensure_worktree(team_id, agent_id)

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(agent, cwd=str(path), git_branch=branch, git_worktree_path=str(path), status=AgentStatus.IDLE.value)

    report = services.cleanup_worktrees(team_id=team_id)

    assert any(item["agent_id"] == agent_id for item in report["deleted_worktrees"])
    assert not path.exists()


def test_cleanup_skips_dirty_orphaned_worktree(tmp_path):
    services = create_git_services(tmp_path)
    team_id = f"dirty-team-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, 1)
    path, branch = services.worktrees.ensure_worktree(team_id, agent_id)
    (path / "orphaned.txt").write_text("dirty")

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(agent, cwd=str(path), git_branch=branch, git_worktree_path=str(path), status=AgentStatus.IDLE.value)

    report = services.cleanup_worktrees(team_id=team_id)

    assert any(item["agent_id"] == agent_id for item in report["skipped_dirty_worktrees"])
    assert path.exists()


def test_inspect_worktrees_detects_conflicts(tmp_path):
    services = create_git_services(tmp_path)
    team_id = f"conflict-team-{uuid4().hex[:6]}"
    services.create_team(team_id, 2)

    for idx in (1, 2):
        agent_id = f"{team_id}-worker-{idx}"
        path, branch = services.worktrees.ensure_worktree(team_id, agent_id)
        (path / "conflict.txt").write_text(f"agent-{idx}")
        with session_scope(services.session_factory) as session:
            repo = Repository(session)
            agent = repo.get_agent(agent_id)
            repo.update_agent(
                agent,
                cwd=str(path),
                git_branch=branch,
                git_worktree_path=str(path),
                status=AgentStatus.RUNNING.value,
                current_task_id=f"task-{idx}",
            )

    report = services.inspect_worktrees(team_id)

    assert len(report["worktree_conflicts"]) == 1
    assert report["worktree_conflicts"][0]["file"] == "conflict.txt"


def test_recover_retry_re_dispatches_blocked_task_on_same_agent(tmp_path):
    services = create_git_services(tmp_path)
    team_id = f"retry-team-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, 1)
    run = services.create_run(team_id, "retry blocked task")
    path, branch = services.worktrees.ensure_worktree(team_id, agent_id)

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(
            agent,
            cwd=str(path),
            git_branch=branch,
            git_worktree_path=str(path),
            status=AgentStatus.BLOCKED.value,
            current_task_id="task-retry-1",
            tmux_pane="%1",
        )
        task = TaskModel(
            task_id="task-retry-1",
            team_id=team_id,
            run_id=run.run_id,
            title="retry task",
            goal="retry this task",
            status=TaskStatus.BLOCKED.value,
            agent_id=agent_id,
        )
        session.add(task)
        session.flush()

    services.tmux.pane_exists = lambda pane_id: True  # type: ignore[method-assign]
    services.codex.dispatch_task = lambda pane_id, contract: "RETRY_PROMPT"  # type: ignore[method-assign]
    report = services.recover(team_id=team_id, retry=True)

    assert any(action["type"] == "retry_attempted" for action in report["actions_taken"])
    assert services.get_task("task-retry-1").status == TaskStatus.RUNNING.value
    events = services.list_events(team_id=team_id, run_id=run.run_id)
    assert any(event.event_type == EventType.TASK_RETRY_REQUESTED.value for event in events)


def test_recover_retry_skips_when_threshold_exceeded(tmp_path):
    services = create_git_services(tmp_path)
    team_id = f"retry-skip-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, 1)
    run = services.create_run(team_id, "retry blocked task")
    path, branch = services.worktrees.ensure_worktree(team_id, agent_id)

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(
            agent,
            cwd=str(path),
            git_branch=branch,
            git_worktree_path=str(path),
            status=AgentStatus.BLOCKED.value,
            current_task_id="task-retry-skip-1",
            tmux_pane="%1",
        )
        task = TaskModel(
            task_id="task-retry-skip-1",
            team_id=team_id,
            run_id=run.run_id,
            title="retry skip task",
            goal="do not retry this task",
            status=TaskStatus.BLOCKED.value,
            agent_id=agent_id,
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

    services.tmux.pane_exists = lambda pane_id: True  # type: ignore[method-assign]
    report = services.recover(team_id=team_id, retry=True)

    assert any(action["type"] == "retry_skipped" and action["reason"] == "retry threshold exceeded" for action in report["actions_taken"])
    assert services.get_task("task-retry-skip-1").status == TaskStatus.BLOCKED.value


def test_retry_candidate_status_matches_recover_threshold_and_conflict_rules(tmp_path):
    services = create_git_services(tmp_path)
    team_id = f"retry-status-{uuid4().hex[:6]}"
    agent_id = f"{team_id}-worker-1"
    services.create_team(team_id, 1)
    run = services.create_run(team_id, "retry candidate status")
    path, branch = services.worktrees.ensure_worktree(team_id, agent_id)

    with session_scope(services.session_factory) as session:
        repo = Repository(session)
        agent = repo.get_agent(agent_id)
        repo.update_agent(
            agent,
            cwd=str(path),
            git_branch=branch,
            git_worktree_path=str(path),
            status=AgentStatus.BLOCKED.value,
            current_task_id="task-retry-status-1",
            tmux_pane="%1",
        )
        task = TaskModel(
            task_id="task-retry-status-1",
            team_id=team_id,
            run_id=run.run_id,
            title="retry status task",
            goal="retry candidate status task",
            status=TaskStatus.BLOCKED.value,
            agent_id=agent_id,
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

    services.tmux.pane_exists = lambda pane_id: True  # type: ignore[method-assign]
    status = services.status(team_id)
    report = services.recover(team_id=team_id, retry=True)

    assert status["retry_candidates"] == []
    assert any(action["type"] == "retry_skipped" and action["reason"] == "retry threshold exceeded" for action in report["actions_taken"])
