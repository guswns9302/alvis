from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import sessionmaker

from app.agents.codex_adapter import CodexAdapter
from app.config import Settings
from app.core.events import event_payload, event_type_name
from app.db.base import session_scope
from app.db.models import AgentModel, ReviewRequestModel, RunModel, TaskModel
from app.db.repository import Repository
from app.enums import AgentRole, AgentStatus, EventType, ReviewStatus, RunStatus, TaskStatus
from app.logging import get_logger
from app.schemas import TaskContract
from app.sessions.tmux_manager import TmuxManager
from app.workspace.worktree_manager import WorktreeManager


class AlvisServices:
    def __init__(self, settings: Settings, session_factory: sessionmaker):
        self.settings = settings
        self.session_factory = session_factory
        self.tmux = TmuxManager(settings.tmux_session_prefix)
        self.codex = CodexAdapter(self.tmux, settings.codex_command, settings.log_dir)
        self.worktrees = WorktreeManager(settings.repo_root, settings.worktree_root)
        self.log = get_logger(__name__)

    def create_team(self, team_id: str, worker_count: int):
        session_name = self.tmux.team_session_name(team_id)
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            team = repo.create_team(team_id, worker_count, session_name)
            repo.append_event(
                team_id=team_id,
                event_type=event_type_name(EventType.RUN_CREATED),
                payload=event_payload("Team created", worker_count=worker_count),
            )
            return team

    def start_team(self, team_id: str):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            team = repo.get_team(team_id)
            if not team:
                raise ValueError(f"team {team_id} not found")
            agents = repo.list_agents(team_id)
            session_name = self.tmux.create_team_layout(team_id, len(agents))
            panes = self.tmux.list_panes(session_name)
            for idx, agent in enumerate(agents):
                pane_id = panes[idx] if idx < len(panes) else None
                worktree_path, branch = self.worktrees.ensure_worktree(team_id, agent.agent_id)
                repo.update_agent(
                    agent,
                    cwd=str(worktree_path),
                    git_branch=branch,
                    git_worktree_path=str(worktree_path),
                    tmux_session=session_name,
                    tmux_window="leader",
                    tmux_pane=pane_id,
                    status=AgentStatus.IDLE.value,
                )
                repo.add_session(team_id, agent.agent_id, session_name, "leader", pane_id)
                repo.append_event(
                    team_id=team_id,
                    agent_id=agent.agent_id,
                    event_type=event_type_name(EventType.SESSION_STARTED),
                    payload=event_payload("Session started", session_name=session_name, pane_id=pane_id),
                )
                if pane_id:
                    self.codex.bootstrap_session(pane_id, str(worktree_path))
            return {"team_id": team_id, "session_name": session_name, "panes": panes}

    def create_run(self, team_id: str, request: str):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.create_run(team_id, request)

    def finalize_run(self, run_id: str, status: RunStatus, final_response: str):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            run = repo.get_run(run_id)
            repo.mark_run_status(run, status, final_response)

    def create_task(self, team_id: str, run_id: str, title: str, goal: str, review_required: bool = False):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.create_task(team_id, run_id, title, goal, review_required)

    def get_task(self, task_id: str) -> TaskModel:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            task = session.get(TaskModel, task_id)
            if not task:
                raise ValueError(f"task {task_id} not found")
            return task

    def assign_task(self, task: TaskModel, agent: AgentModel):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            db_task = session.get(TaskModel, task.task_id)
            db_agent = session.get(AgentModel, agent.agent_id)
            repo.assign_task(db_task, db_agent)
            repo.append_event(
                team_id=db_task.team_id,
                run_id=db_task.run_id,
                task_id=db_task.task_id,
                agent_id=db_agent.agent_id,
                event_type=event_type_name(EventType.TASK_ASSIGNED),
                payload=event_payload("Task assigned", title=db_task.title),
            )

    def dispatch_task(self, agent: AgentModel, contract: TaskContract) -> str:
        if not agent.tmux_pane:
            return self.codex.build_task_prompt(contract)
        return self.codex.dispatch_task(agent.tmux_pane, contract)

    def append_event(self, **kwargs):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            repo.append_event(**kwargs)

    def create_review(self, run_id: str, task_id: str, agent_id: str, summary: str, details: dict):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.create_review(run_id, task_id, agent_id, summary, details)

    def resolve_review(self, review_id: str, approved: bool) -> ReviewRequestModel | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            review = session.get(ReviewRequestModel, review_id)
            if not review:
                return None
            resolved = repo.resolve_review(review, approved)
            task = session.get(TaskModel, resolved.task_id)
            if task:
                repo.update_task(task, status=TaskStatus.DONE.value if approved else TaskStatus.BLOCKED.value)
            repo.append_event(
                team_id=task.team_id if task else "unknown",
                run_id=resolved.run_id,
                task_id=resolved.task_id,
                agent_id=resolved.agent_id,
                event_type=event_type_name(EventType.REVIEW_APPROVED if approved else EventType.REVIEW_REJECTED),
                payload=event_payload("Review resolved", review_id=resolved.review_id, approved=approved),
            )
            return resolved

    def list_reviews(self, status: ReviewStatus | None = None):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_reviews(status)

    def list_events(self, team_id: str | None = None, run_id: str | None = None):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_events(team_id=team_id, run_id=run_id)

    def get_run(self, run_id: str) -> RunModel | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.get_run(run_id)

    def list_team_runs(self, team_id: str) -> list[RunModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_team_runs(team_id)

    def list_worker_agents(self, team_id: str) -> list[AgentModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return [agent for agent in repo.list_agents(team_id) if agent.role == AgentRole.IMPLEMENTER.value]

    def get_agent(self, agent_id: str) -> AgentModel:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            agent = repo.get_agent(agent_id)
            if not agent:
                raise ValueError(f"agent {agent_id} not found")
            return agent

    def status(self, team_id: str) -> dict:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            team = repo.get_team(team_id)
            if not team:
                raise ValueError(f"team {team_id} not found")
            agents = repo.list_agents(team_id)
            runs = repo.list_team_runs(team_id)
            latest_run = runs[0] if runs else None
            tasks = repo.list_run_tasks(latest_run.run_id) if latest_run else []
            return {
                "team_id": team.team_id,
                "session_name": team.session_name,
                "agents": [
                    {
                        "agent_id": agent.agent_id,
                        "role": agent.role,
                        "status": agent.status,
                        "pane": agent.tmux_pane,
                        "cwd": agent.cwd,
                        "task": agent.current_task_id,
                    }
                    for agent in agents
                ],
                "latest_run": None
                if not latest_run
                else {
                    "run_id": latest_run.run_id,
                    "status": latest_run.status,
                    "request": latest_run.request,
                    "final_response": latest_run.final_response,
                },
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "status": task.status,
                        "agent_id": task.agent_id,
                    }
                    for task in tasks
                ],
            }

    def recover(self) -> dict:
        stalled = []
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            for agent in repo.find_stalled_agents(self.settings.heartbeat_timeout_seconds):
                repo.update_agent(agent, status=AgentStatus.BLOCKED.value)
                stalled.append(agent.agent_id)
        return {"stalled_agents": stalled}

    def attach_tmux(self, team_id: str) -> int:
        return self.tmux.attach(self.tmux.team_session_name(team_id))
