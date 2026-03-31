from uuid import uuid4

from app.bootstrap import bootstrap_services
from app.db.base import session_scope
from app.db.models import RunModel
from app.db.repository import Repository
from app.enums import EventType, ReviewStatus, RunStatus, TaskStatus
from app.graph.supervisor import Supervisor, SupervisorDeps


def test_supervisor_creates_run_and_tasks():
    services = bootstrap_services()
    team_id = f"test-team-{uuid4().hex[:8]}"
    services.create_team(team_id, 2)
    state = Supervisor(SupervisorDeps(services=services)).run(team_id, "fix a bug")
    assert state["run_id"].startswith("run-")
    assert len(state["tasks"]) == 3


def test_supervisor_persists_checkpoint_while_waiting_for_review():
    services = bootstrap_services()
    team_id = f"checkpoint-team-{uuid4().hex[:8]}"
    services.create_team(team_id, 2)

    state = Supervisor(SupervisorDeps(services=services)).run(team_id, "fix a bug")

    checkpoint = services.load_checkpoint(state["run_id"])
    assert checkpoint is not None
    assert checkpoint.thread_id == state["run_id"]
    assert checkpoint.next_node == "await_review_resolution"
    assert services.get_run(state["run_id"]).status == RunStatus.WAITING_REVIEW.value


def test_review_reject_creates_replan_task():
    services = bootstrap_services()
    team_id = f"test-team-{uuid4().hex[:8]}"
    services.create_team(team_id, 2)
    state = Supervisor(SupervisorDeps(services=services)).run(team_id, "fix a bug")
    pending_review = next(review for review in services.list_reviews(ReviewStatus.PENDING) if review.run_id == state["run_id"])

    services.resolve_review(pending_review.review_id, approved=False, reason="Need a more specific corrective task")

    tasks = services.list_run_tasks(state["run_id"])
    replan_tasks = [task for task in tasks if task.title.startswith("Replan:")]
    assert len(replan_tasks) == 1
    assert "Need a more specific corrective task" in replan_tasks[0].goal
    replan_payload = services.latest_replan_for_review(pending_review.review_id)
    assert replan_payload is not None
    assert replan_payload["review_id"] == pending_review.review_id


def test_review_approve_resumes_same_run_and_clears_checkpoint():
    services = bootstrap_services()
    team_id = f"resume-team-{uuid4().hex[:8]}"
    services.create_team(team_id, 2)
    supervisor = Supervisor(SupervisorDeps(services=services))
    state = supervisor.run(team_id, "fix a bug")
    pending_review = next(review for review in services.list_reviews(ReviewStatus.PENDING) if review.run_id == state["run_id"])

    resolved = services.resolve_review(pending_review.review_id, approved=True)
    resumed = supervisor.resume(state["run_id"])

    assert resolved is not None
    assert resumed["run_id"] == state["run_id"]
    assert resumed["status"] == RunStatus.DONE.value
    assert services.get_run(state["run_id"]).status == RunStatus.DONE.value
    assert services.load_checkpoint(state["run_id"]) is None


def test_recover_blocks_missing_pane_and_orphaned_task():
    services = bootstrap_services()
    team_id = f"recover-team-{uuid4().hex[:8]}"
    services.create_team(team_id, 2)
    state = Supervisor(SupervisorDeps(services=services)).run(team_id, "fix a bug")
    target_task = state["tasks"][0]["task_id"]

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


def test_recover_reconciles_dangling_run_to_done():
    services = bootstrap_services()
    team_id = f"dangling-team-{uuid4().hex[:8]}"
    services.create_team(team_id, 2)
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
    from app.config import Settings, ensure_runtime_dirs
    from app.db.base import create_session_factory
    from app.db.init_db import init_db
    from app.services import AlvisServices

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
    services.create_team(team_id, 2)
    run = services.create_run(team_id, "conflict run")

    supervisor = Supervisor(SupervisorDeps(services=services))
    supervisor.create_plan = lambda request: [{"title": "Implement changes", "goal": request, "worker_index": 0}]  # type: ignore[method-assign]
    services.inspect_worktrees = lambda team: {  # type: ignore[method-assign]
        "worktrees": [],
        "cleanup_candidates": [],
        "dirty_orphaned_worktrees": [],
        "worktree_conflicts": [{"file": "shared.py", "owners": [{"agent_id": f"{team_id}-worker-1", "task_id": "existing-task", "path": "/tmp"}]}],
    }
    state = supervisor.run(team_id, "conflict run")
    created_task_id = state["tasks"][0]["task_id"]
    created_task = services.get_task(created_task_id)
    events = services.list_events(team_id=team_id, run_id=state["run_id"])

    assert created_task.status == TaskStatus.BLOCKED.value
    assert created_task.agent_id is None
    assert not any(event.event_type == EventType.TASK_ASSIGNED.value and event.task_id == created_task_id for event in events)
