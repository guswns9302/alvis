from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.agents.codex_adapter import CodexAdapter
from app.config import Settings
from app.core.events import event_payload, event_type_name
from app.db.base import session_scope
from app.db.models import AgentModel, ReviewRequestModel, RunModel, TaskModel
from app.db.repository import Repository
from app.enums import AgentRole, AgentStatus, EventType, ReviewStatus, RunStatus, TaskStatus
from app.logging import get_logger
from app.runtime.output_collector import OutputCollector
from app.schemas import AgentOutput, DispatchResult, ReplanResult, TaskContract
from app.sessions.tmux_manager import TmuxManager
from app.workspace.worktree_manager import WorktreeManager


class AlvisServices:
    ACTIVE_TASK_STATUSES = {
        TaskStatus.ASSIGNED.value,
        TaskStatus.RUNNING.value,
        TaskStatus.WAITING_REVIEW.value,
    }
    ACTIVE_AGENT_STATUSES = {
        AgentStatus.ASSIGNED.value,
        AgentStatus.RUNNING.value,
        AgentStatus.WAITING_REVIEW.value,
    }

    def __init__(self, settings: Settings, session_factory: sessionmaker):
        self.settings = settings
        self.session_factory = session_factory
        self.tmux = TmuxManager(settings.tmux_session_prefix)
        self.codex = CodexAdapter(
            self.tmux,
            settings.codex_command,
            settings.log_dir,
            settings.repo_root,
            settings.runtime_dir,
        )
        self.worktrees = WorktreeManager(settings.repo_root, settings.worktree_root)
        self.output_collector = OutputCollector()
        self.log = get_logger(__name__)

    def create_team(self, team_id: str, worker_count: int):
        session_name = self.tmux.team_session_name(team_id)
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            team = repo.create_team(team_id, worker_count, session_name)
            repo.append_event(
                team_id=team_id,
                event_type=event_type_name(EventType.TEAM_CREATED),
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
                    runtime_paths = self.codex.bootstrap_session(agent.agent_id, pane_id, str(worktree_path))
                    repo.append_event(
                        team_id=team_id,
                        agent_id=agent.agent_id,
                        event_type=event_type_name(EventType.AGENT_HEARTBEAT),
                        payload=event_payload("Runtime paths created", **runtime_paths),
                    )
            return {"team_id": team_id, "session_name": session_name, "panes": panes}

    def create_run(self, team_id: str, request: str):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.create_run(team_id, request)

    def finalize_run(self, run_id: str, status: RunStatus, final_response: str | None = None):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            run = repo.get_run(run_id)
            if not run:
                raise ValueError(f"run {run_id} not found")
            repo.mark_run_status(run, status, final_response)

    def get_run(self, run_id: str) -> RunModel | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.get_run(run_id)

    def get_review(self, review_id: str) -> ReviewRequestModel | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.get_review(review_id)

    def list_team_runs(self, team_id: str) -> list[RunModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_team_runs(team_id)

    def create_task(self, team_id: str, run_id: str, title: str, goal: str, review_required: bool = False):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.create_task(team_id, run_id, title, goal, review_required)

    def get_task(self, task_id: str) -> TaskModel:
        with session_scope(self.session_factory) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                raise ValueError(f"task {task_id} not found")
            return task

    def list_run_tasks(self, run_id: str) -> list[TaskModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_run_tasks(run_id)

    def update_task(self, task_id: str, **values) -> TaskModel:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            task = session.get(TaskModel, task_id)
            if not task:
                raise ValueError(f"task {task_id} not found")
            updated = repo.update_task(task, **values)
            if updated.agent_id and "status" in values:
                agent = session.get(AgentModel, updated.agent_id)
                if agent:
                    agent_status = agent.status
                    if values["status"] == TaskStatus.DONE.value:
                        agent_status = AgentStatus.DONE.value
                    elif values["status"] == TaskStatus.WAITING_REVIEW.value:
                        agent_status = AgentStatus.WAITING_REVIEW.value
                    elif values["status"] == TaskStatus.BLOCKED.value:
                        agent_status = AgentStatus.BLOCKED.value
                    repo.update_agent(agent, status=agent_status)
                    repo.append_event(
                        team_id=updated.team_id,
                        run_id=updated.run_id,
                        task_id=updated.task_id,
                        agent_id=agent.agent_id,
                        event_type=event_type_name(EventType.AGENT_STATUS_CHANGED),
                        payload=event_payload("Agent status updated", status=agent_status),
                    )
            return updated

    def assign_task(self, task_id: str, agent_id: str):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            task = session.get(TaskModel, task_id)
            agent = session.get(AgentModel, agent_id)
            if not task or not agent:
                raise ValueError("task or agent not found")
            repo.assign_task(task, agent)
            repo.update_agent(agent, status=AgentStatus.RUNNING.value)
            repo.append_event(
                team_id=task.team_id,
                run_id=task.run_id,
                task_id=task.task_id,
                agent_id=agent.agent_id,
                event_type=event_type_name(EventType.TASK_ASSIGNED),
                payload=event_payload("Task assigned", title=task.title),
            )
            repo.append_event(
                team_id=task.team_id,
                run_id=task.run_id,
                task_id=task.task_id,
                agent_id=agent.agent_id,
                event_type=event_type_name(EventType.AGENT_STATUS_CHANGED),
                payload=event_payload("Agent running", status=AgentStatus.RUNNING.value),
            )

    def get_agent(self, agent_id: str) -> AgentModel:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            agent = repo.get_agent(agent_id)
            if not agent:
                raise ValueError(f"agent {agent_id} not found")
            return agent

    def list_worker_agents(self, team_id: str) -> list[AgentModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return [agent for agent in repo.list_agents(team_id) if agent.role == AgentRole.IMPLEMENTER.value]

    def _default_task_contract(self, task: TaskModel, agent: AgentModel) -> TaskContract:
        return TaskContract(
            task_id=task.task_id,
            role=agent.role,
            cwd=agent.cwd or str(self.settings.repo_root),
            goal=task.goal,
            constraints=[
                "Do not push changes.",
                "Escalate review before commit.",
                "Stay within assigned worktree.",
            ],
            expected_output=[
                "Summary",
                "Changed files",
                "Test results",
                "Risks or blockers",
            ],
            context={"team_id": task.team_id, "run_id": task.run_id},
        )

    def _retry_count(self, repo: Repository, task_id: str) -> int:
        return len(
            [
                event
                for event in repo.list_events()
                if event.task_id == task_id and event.event_type == event_type_name(EventType.TASK_RETRY_REQUESTED)
            ]
        )

    def _blocking_conflicts_for_agent(self, team_id: str, agent_id: str) -> list[dict]:
        worktree_report = self.inspect_worktrees(team_id)
        return [
            conflict
            for conflict in worktree_report["worktree_conflicts"]
            if any(owner["agent_id"] == agent_id for owner in conflict["owners"])
        ]

    def can_dispatch_task(self, task_id: str, agent_id: str) -> DispatchResult:
        task = self.get_task(task_id)
        agent = self.get_agent(agent_id)
        blocking_conflicts = self._blocking_conflicts_for_agent(agent.team_id, agent_id)
        if blocking_conflicts:
            self.update_task(task_id, status=TaskStatus.BLOCKED.value, result_summary="Dispatch blocked by worktree conflict.")
            self.append_event(
                team_id=agent.team_id,
                run_id=task.run_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type=event_type_name(EventType.WORKTREE_CONFLICT_DETECTED),
                payload=event_payload("Dispatch blocked by worktree conflict", conflicts=blocking_conflicts),
            )
            return DispatchResult(ok=False, reason="worktree_conflict")
        return DispatchResult(ok=True)

    def inspect_worktrees(self, team_id: str) -> dict:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            team = repo.get_team(team_id)
            if not team:
                raise ValueError(f"team {team_id} not found")
            agents = repo.list_agents(team_id)
            runs = repo.list_team_runs(team_id)
            tasks = []
            for run in runs:
                tasks.extend(repo.list_run_tasks(run.run_id))
            task_map = {task.task_id: task for task in tasks}
            inspections = []
            cleanup_candidates = []
            dirty_orphaned = []
            active_entries = []

            for agent in agents:
                worktree_path = Path(agent.git_worktree_path) if agent.git_worktree_path else self.worktrees.worktree_path(team_id, agent.agent_id)
                inspected = self.worktrees.inspect(worktree_path)
                pane_alive = bool(agent.tmux_pane) and self.tmux.pane_exists(agent.tmux_pane)
                current_task = task_map.get(agent.current_task_id) if agent.current_task_id else None
                has_active_task = bool(current_task and current_task.status in self.ACTIVE_TASK_STATUSES)
                orphaned = (not pane_alive) and agent.status not in self.ACTIVE_AGENT_STATUSES and not has_active_task
                entry = {
                    "agent_id": agent.agent_id,
                    "path": str(inspected.path),
                    "exists": inspected.exists,
                    "branch": inspected.branch,
                    "clean": inspected.clean,
                    "changed_files": inspected.changed_files,
                    "status": agent.status,
                    "task_id": agent.current_task_id,
                    "pane_alive": pane_alive,
                    "orphaned": orphaned,
                }
                inspections.append(entry)
                if agent.status in self.ACTIVE_AGENT_STATUSES and inspected.changed_files:
                    active_entries.append(entry)
                if orphaned and inspected.exists:
                    if inspected.clean:
                        cleanup_candidates.append(entry)
                    else:
                        dirty_orphaned.append(entry)

            conflict_map: dict[str, list[dict]] = {}
            for entry in active_entries:
                for file_path in entry["changed_files"]:
                    conflict_map.setdefault(file_path, []).append(
                        {"agent_id": entry["agent_id"], "task_id": entry["task_id"], "path": entry["path"]}
                    )

            conflicts = []
            for file_path, owners in conflict_map.items():
                if len(owners) < 2:
                    continue
                conflicts.append({"file": file_path, "owners": owners})

            return {
                "worktrees": inspections,
                "cleanup_candidates": cleanup_candidates,
                "dirty_orphaned_worktrees": dirty_orphaned,
                "worktree_conflicts": conflicts,
            }

    def cleanup_worktrees(self, team_id: str | None = None) -> dict:
        teams = [team_id] if team_id else [team.team_id for team in self._list_teams()]
        deleted = []
        skipped_dirty = []
        skipped_active = []
        for target_team in teams:
            report = self.inspect_worktrees(target_team)
            candidate_paths = {item["path"] for item in report["cleanup_candidates"]}
            for entry in report["worktrees"]:
                if not entry["orphaned"] or not entry["exists"]:
                    if entry["exists"] and entry["changed_files"] and not entry["orphaned"]:
                        skipped_active.append(entry)
                    continue
                if entry["path"] in candidate_paths:
                    self.worktrees.remove(Path(entry["path"]))
                    deleted.append(entry)
                elif not entry["clean"]:
                    skipped_dirty.append(entry)
        return {
            "deleted_worktrees": deleted,
            "skipped_dirty_worktrees": skipped_dirty,
            "skipped_active_worktrees": skipped_active,
        }

    def _list_teams(self):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            seen = set()
            teams = []
            for agent in repo.list_all_agents():
                if agent.team_id in seen:
                    continue
                team = repo.get_team(agent.team_id)
                if team is not None:
                    teams.append(team)
                    seen.add(agent.team_id)
            return teams

    def dispatch_task(self, agent_id: str, contract: TaskContract) -> DispatchResult:
        agent = self.get_agent(agent_id)
        gate = self.can_dispatch_task(contract.task_id, agent_id)
        if not gate.ok:
            return gate
        if not agent.tmux_pane:
            return DispatchResult(ok=True, prompt=self.codex.build_task_prompt(contract))
        return DispatchResult(ok=True, prompt=self.codex.dispatch_task(agent.tmux_pane, contract))

    def append_event(self, **kwargs):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            repo.append_event(**kwargs)

    def list_events(self, team_id: str | None = None, run_id: str | None = None):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_events(team_id=team_id, run_id=run_id)

    def latest_replan_for_review(self, review_id: str) -> dict | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            events = [
                event
                for event in repo.list_events()
                if event.event_type == event_type_name(EventType.REPLAN_GENERATED)
                and event.payload.get("review_id") == review_id
            ]
            if not events:
                return None
            latest = events[-1]
            return latest.payload

    def create_review(self, run_id: str, task_id: str, agent_id: str, summary: str, details: dict):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.create_review(run_id, task_id, agent_id, summary, details)

    def list_reviews(self, status: ReviewStatus | None = None):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_reviews(status)

    def list_run_reviews(self, run_id: str, status: ReviewStatus | None = None) -> list[ReviewRequestModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            reviews = repo.list_reviews(status)
            return [review for review in reviews if review.run_id == run_id]

    def list_active_run_tasks(self, run_id: str) -> list[TaskModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return [
                task
                for task in repo.list_run_tasks(run_id)
                if task.status in {TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value}
            ]

    def save_checkpoint(self, run_id: str, thread_id: str, next_node: str, state: dict):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.save_checkpoint(run_id, thread_id, next_node, state)

    def load_checkpoint(self, run_id: str):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.get_checkpoint(run_id)

    def clear_checkpoint(self, run_id: str) -> None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            repo.delete_checkpoint(run_id)

    def _choose_replan_agent(self, repo: Repository, team_id: str, original_agent_id: str | None) -> AgentModel:
        workers = [agent for agent in repo.list_agents(team_id) if agent.role == AgentRole.IMPLEMENTER.value]
        for agent in workers:
            if agent.agent_id != original_agent_id and agent.status != AgentStatus.BLOCKED.value:
                return agent
        if original_agent_id:
            original = repo.get_agent(original_agent_id)
            if original:
                return original
        leader = repo.get_agent(f"{team_id}-leader")
        if not leader:
            raise ValueError(f"team {team_id} has no replan target agent")
        return leader

    def _build_replan_goal(self, task: TaskModel, reason: str, details: dict) -> str:
        summary = details.get("summary") or task.result_summary or "No previous output summary."
        return (
            f"Revise the previous task after rejected review.\n"
            f"Original task: {task.title}\n"
            f"Original goal: {task.goal}\n"
            f"Reject reason: {reason}\n"
            f"Previous summary: {summary}"
        )

    def _request_replan(
        self,
        repo: Repository,
        *,
        run: RunModel,
        task: TaskModel,
        review: ReviewRequestModel,
        reason: str,
    ) -> ReplanResult:
        replan_agent = self._choose_replan_agent(repo, task.team_id, task.agent_id)
        repo.append_event(
            team_id=task.team_id,
            run_id=run.run_id,
            task_id=task.task_id,
            agent_id=task.agent_id,
            event_type=event_type_name(EventType.REPLAN_REQUESTED),
            payload=event_payload(
                "Replan requested",
                review_id=review.review_id,
                parent_task_id=task.task_id,
                reason=reason,
            ),
        )
        new_task = repo.create_task(
            team_id=task.team_id,
            run_id=run.run_id,
            title=f"Replan: {task.title}",
            goal=self._build_replan_goal(task, reason, review.details or {}),
            review_required=False,
        )
        repo.append_event(
            team_id=task.team_id,
            run_id=run.run_id,
            task_id=new_task.task_id,
            agent_id=replan_agent.agent_id,
            event_type=event_type_name(EventType.REPLAN_GENERATED),
            payload=event_payload(
                "Replan task created",
                review_id=review.review_id,
                parent_task_id=task.task_id,
                new_task_id=new_task.task_id,
                assigned_agent_id=replan_agent.agent_id,
                reason=reason,
            ),
        )
        blocking_conflicts = self._blocking_conflicts_for_agent(task.team_id, replan_agent.agent_id)
        if blocking_conflicts:
            repo.update_task(
                new_task,
                status=TaskStatus.BLOCKED.value,
                result_summary="Dispatch blocked by worktree conflict.",
            )
            repo.append_event(
                team_id=task.team_id,
                run_id=run.run_id,
                task_id=new_task.task_id,
                agent_id=replan_agent.agent_id,
                event_type=event_type_name(EventType.WORKTREE_CONFLICT_DETECTED),
                payload=event_payload("Dispatch blocked by worktree conflict", conflicts=blocking_conflicts),
            )
            return ReplanResult(
                review_id=review.review_id,
                parent_task_id=task.task_id,
                new_task_id=new_task.task_id,
                assigned_agent_id=replan_agent.agent_id,
                reason=reason,
            )
        repo.assign_task(new_task, replan_agent)
        repo.update_agent(replan_agent, status=AgentStatus.RUNNING.value)
        contract = TaskContract(
            task_id=new_task.task_id,
            role=replan_agent.role,
            cwd=replan_agent.cwd or str(self.settings.repo_root),
            goal=new_task.goal,
            constraints=[
                "This task was created because review was rejected.",
                "Address the rejection reason directly.",
                "Do not push changes.",
                "Escalate review before commit.",
            ],
            expected_output=[
                "Summary",
                "Changed files",
                "Test results",
                "How the rejection reason was addressed",
            ],
            context={
                "team_id": task.team_id,
                "run_id": run.run_id,
                "source_review_id": review.review_id,
                "parent_task_id": task.task_id,
                "rejection_reason": reason,
            },
        )
        if replan_agent.tmux_pane:
            prompt = self.codex.dispatch_task(replan_agent.tmux_pane, contract)
        else:
            prompt = self.codex.build_task_prompt(contract)
        repo.append_event(
            team_id=task.team_id,
            run_id=run.run_id,
            task_id=new_task.task_id,
            agent_id=replan_agent.agent_id,
            event_type=event_type_name(EventType.AGENT_PROMPT_SENT),
            payload=event_payload(
                "Replan task dispatched",
                prompt=prompt,
                review_id=review.review_id,
                parent_task_id=task.task_id,
            ),
        )
        return ReplanResult(
            review_id=review.review_id,
            parent_task_id=task.task_id,
            new_task_id=new_task.task_id,
            assigned_agent_id=replan_agent.agent_id,
            reason=reason,
        )

    def resolve_review(self, review_id: str, approved: bool, reason: str | None = None) -> ReviewRequestModel | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            review = session.get(ReviewRequestModel, review_id)
            if not review:
                return None
            resolved = repo.resolve_review(review, approved)
            task = session.get(TaskModel, resolved.task_id)
            run = session.get(RunModel, resolved.run_id)
            agent = session.get(AgentModel, resolved.agent_id)
            if task:
                repo.update_task(task, status=TaskStatus.DONE.value if approved else TaskStatus.BLOCKED.value)
            if agent:
                repo.update_agent(
                    agent,
                    status=AgentStatus.DONE.value if approved else AgentStatus.BLOCKED.value,
                )
            if run:
                pending_for_run = [item for item in repo.list_reviews(ReviewStatus.PENDING) if item.run_id == resolved.run_id]
                if approved:
                    repo.mark_run_status(
                        run,
                        RunStatus.WAITING_REVIEW if pending_for_run else RunStatus.RUNNING,
                        "Review approved; still waiting for pending reviews." if pending_for_run else "Review approved; resuming run.",
                    )
                else:
                    repo.mark_run_status(run, RunStatus.RUNNING, "Review rejected; replan requested.")
            repo.append_event(
                team_id=task.team_id if task else "unknown",
                run_id=resolved.run_id,
                task_id=resolved.task_id,
                agent_id=resolved.agent_id,
                event_type=event_type_name(EventType.REVIEW_APPROVED if approved else EventType.REVIEW_REJECTED),
                payload=event_payload(
                    "Review resolved",
                    review_id=resolved.review_id,
                    approved=approved,
                    reason=reason,
                ),
            )
            if approved and task:
                repo.append_event(
                    team_id=task.team_id,
                    run_id=resolved.run_id,
                    task_id=resolved.task_id,
                    agent_id=resolved.agent_id,
                    event_type=event_type_name(EventType.RUN_RESUMED),
                    payload=event_payload("Run resumed after review approval", review_id=resolved.review_id),
                )
            elif not approved and task and run:
                replan_reason = reason or review.details.get("reason") or "Rejected review requires follow-up task."
                self._request_replan(
                    repo,
                    run=run,
                    task=task,
                    review=resolved,
                    reason=replan_reason,
                )
            return resolved

    def record_heartbeat(self, agent_id: str, heartbeat_at: float | None = None) -> None:
        at = heartbeat_at or datetime.now(timezone.utc).timestamp()
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            agent = repo.get_agent(agent_id)
            if not agent:
                raise ValueError(f"agent {agent_id} not found")
            repo.update_agent(
                agent,
                last_heartbeat_at=datetime.fromtimestamp(at, tz=timezone.utc).replace(tzinfo=None),
            )
            repo.append_event(
                team_id=agent.team_id,
                agent_id=agent.agent_id,
                task_id=agent.current_task_id,
                event_type=event_type_name(EventType.AGENT_HEARTBEAT),
                payload=event_payload("Heartbeat recorded", heartbeat_at=at),
            )

    def refresh_agent_runtime(self, agent_id: str) -> AgentOutput | None:
        agent = self.get_agent(agent_id)
        paths = self.codex.session_paths(agent_id)
        snapshot = self.output_collector.read_snapshot(paths["stdout"], paths["heartbeat"])
        if snapshot.heartbeat_at:
            self.record_heartbeat(agent_id, snapshot.heartbeat_at)
        task_id = agent.current_task_id
        if not task_id:
            return None
        output = self.output_collector.summarize_task_output(
            agent_id=agent_id,
            task_id=task_id,
            log_text=snapshot.log_text,
        )
        self.append_event(
            team_id=agent.team_id,
            run_id=self.get_task(task_id).run_id,
            task_id=task_id,
            agent_id=agent_id,
            event_type=event_type_name(
                EventType.AGENT_OUTPUT_FINAL if output.kind == "final" else EventType.AGENT_OUTPUT_DELTA
            ),
            payload=output.model_dump(),
        )
        return output

    def collect_outputs(self, team_id: str) -> list[dict]:
        outputs = []
        for agent in self.list_worker_agents(team_id):
            output = self.refresh_agent_runtime(agent.agent_id)
            if output:
                outputs.append(output.model_dump())
        try:
            leader = self.get_agent(f"{team_id}-leader")
            leader_output = self.refresh_agent_runtime(leader.agent_id)
            if leader_output:
                outputs.append(leader_output.model_dump())
        except ValueError:
            pass
        return outputs

    def get_task_output(self, task_id: str) -> AgentOutput | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            event = repo.latest_event(task_id=task_id, event_type=event_type_name(EventType.AGENT_OUTPUT_FINAL))
            if not event:
                event = repo.latest_event(task_id=task_id, event_type=event_type_name(EventType.AGENT_OUTPUT_DELTA))
            if not event:
                return None
            return AgentOutput(**event.payload)

    def inspect_runtime_state(self, team_id: str | None = None) -> dict:
        stale_heartbeat = []
        missing_panes = []
        orphaned_tasks = []
        orphaned_reviews = []
        dangling_runs = []
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            agents = repo.list_agents(team_id) if team_id else repo.list_all_agents()
            runs = repo.list_team_runs(team_id) if team_id else repo.list_all_runs()
            tasks = []
            for run in runs:
                tasks.extend(repo.list_run_tasks(run.run_id))
            pending_reviews = repo.list_reviews(ReviewStatus.PENDING)
            if team_id:
                run_ids = {run.run_id for run in runs}
                pending_reviews = [review for review in pending_reviews if review.run_id in run_ids]

            stale_agent_ids = {agent.agent_id for agent in repo.find_stalled_agents(self.settings.heartbeat_timeout_seconds)}
            agent_by_id = {agent.agent_id: agent for agent in agents}

            for agent in agents:
                if agent.tmux_pane and not self.tmux.pane_exists(agent.tmux_pane):
                    missing_panes.append(agent.agent_id)
                if agent.agent_id in stale_agent_ids:
                    stale_heartbeat.append(agent.agent_id)

            for task in tasks:
                if task.status not in self.ACTIVE_TASK_STATUSES:
                    continue
                if not task.agent_id:
                    orphaned_tasks.append(task.task_id)
                    continue
                agent = agent_by_id.get(task.agent_id)
                if not agent:
                    orphaned_tasks.append(task.task_id)
                    continue
                if agent.current_task_id != task.task_id:
                    orphaned_tasks.append(task.task_id)
                    continue
                if agent.tmux_pane and not self.tmux.pane_exists(agent.tmux_pane):
                    orphaned_tasks.append(task.task_id)
                    continue
                if task.status != TaskStatus.WAITING_REVIEW.value and agent.agent_id in stale_agent_ids:
                    orphaned_tasks.append(task.task_id)

            task_by_id = {task.task_id: task for task in tasks}
            run_by_id = {run.run_id: run for run in runs}
            for review in pending_reviews:
                task = task_by_id.get(review.task_id)
                run = run_by_id.get(review.run_id)
                if not task or not run:
                    orphaned_reviews.append(review.review_id)
                    continue
                if task.status != TaskStatus.WAITING_REVIEW.value or run.status == RunStatus.DONE.value:
                    orphaned_reviews.append(review.review_id)

            for run in runs:
                if run.status not in {RunStatus.RUNNING.value, RunStatus.WAITING_REVIEW.value}:
                    continue
                run_tasks = [task for task in tasks if task.run_id == run.run_id]
                has_active_tasks = any(task.status in self.ACTIVE_TASK_STATUSES for task in run_tasks)
                has_pending_reviews = any(review.run_id == run.run_id for review in pending_reviews)
                if not has_active_tasks and not has_pending_reviews:
                    dangling_runs.append(run.run_id)

        return {
            "missing_panes": sorted(set(missing_panes)),
            "stale_heartbeat": sorted(set(stale_heartbeat)),
            "orphaned_tasks": sorted(set(orphaned_tasks)),
            "orphaned_reviews": sorted(set(orphaned_reviews)),
            "dangling_runs": sorted(set(dangling_runs)),
        }

    def _retry_blocked_tasks(self, repo: Repository, tasks: list[TaskModel], agent_map: dict[str, AgentModel]) -> list[dict]:
        actions_taken = []
        for task in tasks:
            if task.status != TaskStatus.BLOCKED.value or not task.agent_id:
                continue
            agent = agent_map.get(task.agent_id)
            if not agent or not agent.tmux_pane or not self.tmux.pane_exists(agent.tmux_pane):
                repo.append_event(
                    team_id=task.team_id,
                    run_id=task.run_id,
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    event_type=event_type_name(EventType.TASK_RETRY_SKIPPED),
                    payload=event_payload("Retry skipped", reason="pane unavailable"),
                )
                actions_taken.append({"type": "retry_skipped", "task_id": task.task_id, "reason": "pane unavailable"})
                continue

            retry_count = self._retry_count(repo, task.task_id)
            if retry_count >= self.settings.review_retry_threshold:
                repo.append_event(
                    team_id=task.team_id,
                    run_id=task.run_id,
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    event_type=event_type_name(EventType.TASK_RETRY_SKIPPED),
                    payload=event_payload("Retry skipped", reason="retry threshold exceeded", retry_count=retry_count),
                )
                actions_taken.append({"type": "retry_skipped", "task_id": task.task_id, "reason": "retry threshold exceeded"})
                continue

            if self._blocking_conflicts_for_agent(task.team_id, task.agent_id):
                repo.append_event(
                    team_id=task.team_id,
                    run_id=task.run_id,
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    event_type=event_type_name(EventType.TASK_RETRY_SKIPPED),
                    payload=event_payload("Retry skipped", reason="worktree conflict"),
                )
                actions_taken.append({"type": "retry_skipped", "task_id": task.task_id, "reason": "worktree conflict"})
                continue

            repo.append_event(
                team_id=task.team_id,
                run_id=task.run_id,
                task_id=task.task_id,
                agent_id=task.agent_id,
                event_type=event_type_name(EventType.TASK_RETRY_REQUESTED),
                payload=event_payload("Retry requested", retry_count=retry_count + 1),
            )
            dispatch = self.dispatch_task(agent.agent_id, self._default_task_contract(task, agent))
            if not dispatch.ok:
                repo.append_event(
                    team_id=task.team_id,
                    run_id=task.run_id,
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    event_type=event_type_name(EventType.TASK_RETRY_SKIPPED),
                    payload=event_payload("Retry skipped", reason=dispatch.reason or "dispatch failed"),
                )
                actions_taken.append({"type": "retry_skipped", "task_id": task.task_id, "reason": dispatch.reason or "dispatch failed"})
                continue

            repo.update_task(task, status=TaskStatus.RUNNING.value)
            repo.update_agent(agent, status=AgentStatus.RUNNING.value, current_task_id=task.task_id)
            repo.append_event(
                team_id=task.team_id,
                run_id=task.run_id,
                task_id=task.task_id,
                agent_id=task.agent_id,
                event_type=event_type_name(EventType.TASK_RETRY_SUCCEEDED),
                payload=event_payload("Retry dispatched", prompt=dispatch.prompt, retry_count=retry_count + 1),
            )
            run = repo.get_run(task.run_id)
            if run:
                repo.mark_run_status(run, RunStatus.RUNNING, "Retry dispatched after recovery.")
            actions_taken.append({"type": "retry_attempted", "task_id": task.task_id, "agent_id": task.agent_id})
        return actions_taken

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
            checkpoint = repo.get_checkpoint(latest_run.run_id) if latest_run else None
            worktree_report = self.inspect_worktrees(team_id)
            retry_candidates = []
            for task in tasks:
                if task.status != TaskStatus.BLOCKED.value or not task.agent_id:
                    continue
                agent = next((item for item in agents if item.agent_id == task.agent_id), None)
                if not agent or not agent.tmux_pane or not self.tmux.pane_exists(agent.tmux_pane):
                    continue
                if self._retry_count(repo, task.task_id) >= self.settings.review_retry_threshold:
                    continue
                if self._blocking_conflicts_for_agent(team_id, agent.agent_id):
                    continue
                if agent and agent.tmux_pane and self.tmux.pane_exists(agent.tmux_pane):
                    retry_candidates.append({"task_id": task.task_id, "agent_id": agent.agent_id})
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
                        "last_heartbeat_at": None if not agent.last_heartbeat_at else agent.last_heartbeat_at.isoformat(),
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
                    "checkpoint": None
                    if not checkpoint
                    else {
                        "thread_id": checkpoint.thread_id,
                        "next_node": checkpoint.next_node,
                        "updated_at": checkpoint.updated_at.isoformat(),
                    },
                },
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "status": task.status,
                        "agent_id": task.agent_id,
                        "result_summary": task.result_summary,
                    }
                    for task in tasks
                ],
                "pending_reviews": [
                    {
                        "review_id": review.review_id,
                        "task_id": review.task_id,
                        "agent_id": review.agent_id,
                        "status": review.status,
                        "summary": review.summary,
                    }
                    for review in repo.list_reviews(ReviewStatus.PENDING)
                    if review.run_id in {run.run_id for run in runs}
                ],
                "replan_events": [
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "task_id": event.task_id,
                        "agent_id": event.agent_id,
                        "payload": event.payload,
                    }
                    for event in repo.list_events(team_id=team_id)
                    if event.event_type in {
                        event_type_name(EventType.REPLAN_REQUESTED),
                        event_type_name(EventType.REPLAN_GENERATED),
                    }
                ],
                "runtime_issues": self.inspect_runtime_state(team_id),
                "worktree_conflicts": worktree_report["worktree_conflicts"],
                "cleanup_candidates": worktree_report["cleanup_candidates"],
                "dirty_orphaned_worktrees": worktree_report["dirty_orphaned_worktrees"],
                "retry_candidates": retry_candidates,
            }

    def recover(self, team_id: str | None = None, retry: bool = False) -> dict:
        inspection = self.inspect_runtime_state(team_id)
        actions_taken: list[dict] = []
        reconciled_runs: list[str] = []
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            agents = repo.list_agents(team_id) if team_id else repo.list_all_agents()
            agent_map = {agent.agent_id: agent for agent in agents}
            runs = repo.list_team_runs(team_id) if team_id else repo.list_all_runs()
            run_map = {run.run_id: run for run in runs}
            tasks = []
            for run in runs:
                tasks.extend(repo.list_run_tasks(run.run_id))
            task_map = {task.task_id: task for task in tasks}

            for agent_id in inspection["missing_panes"]:
                agent = agent_map.get(agent_id)
                if not agent:
                    continue
                repo.update_agent(agent, status=AgentStatus.BLOCKED.value)
                actions_taken.append({"type": "agent_blocked_missing_pane", "agent_id": agent_id})
                if agent.current_task_id and agent.current_task_id in task_map:
                    repo.update_task(task_map[agent.current_task_id], status=TaskStatus.BLOCKED.value)
                    actions_taken.append({"type": "task_blocked_missing_pane", "task_id": agent.current_task_id})

            for agent_id in inspection["stale_heartbeat"]:
                agent = agent_map.get(agent_id)
                if not agent or agent.status == AgentStatus.BLOCKED.value:
                    continue
                repo.update_agent(agent, status=AgentStatus.BLOCKED.value)
                actions_taken.append({"type": "agent_blocked_stale_heartbeat", "agent_id": agent_id})

            for task_id in inspection["orphaned_tasks"]:
                task = task_map.get(task_id)
                if not task:
                    continue
                repo.update_task(task, status=TaskStatus.BLOCKED.value)
                actions_taken.append({"type": "task_blocked_orphaned", "task_id": task_id})
                if task.agent_id and task.agent_id in agent_map:
                    repo.update_agent(agent_map[task.agent_id], status=AgentStatus.BLOCKED.value)
                    actions_taken.append({"type": "agent_blocked_orphaned_task", "agent_id": task.agent_id})

            for review_id in inspection["orphaned_reviews"]:
                review = session.get(ReviewRequestModel, review_id)
                if not review:
                    continue
                repo.resolve_review(review, approved=False)
                actions_taken.append({"type": "review_rejected_orphaned", "review_id": review_id})

            for run_id in inspection["dangling_runs"]:
                run = run_map.get(run_id)
                if not run:
                    continue
                run_tasks = [task for task in tasks if task.run_id == run_id]
                has_blocked = any(task.status in {TaskStatus.BLOCKED.value, TaskStatus.FAILED.value} for task in run_tasks)
                target_status = RunStatus.FAILED if has_blocked else RunStatus.DONE
                repo.mark_run_status(
                    run,
                    target_status,
                    "Recovered dangling run with blocked tasks." if has_blocked else "Recovered dangling run with no active work.",
                )
                reconciled_runs.append(run_id)
                actions_taken.append({"type": "run_reconciled", "run_id": run_id, "status": target_status.value})

            retry_actions = self._retry_blocked_tasks(repo, tasks, agent_map) if retry else []
            actions_taken.extend(retry_actions)

        conflict_report = {}
        cleanup_report = {}
        if team_id:
            conflict_report = self.inspect_worktrees(team_id)
            cleanup_report = {
                "cleanup_candidates": conflict_report["cleanup_candidates"],
                "dirty_orphaned_worktrees": conflict_report["dirty_orphaned_worktrees"],
            }

        return {
            **inspection,
            "actions_taken": actions_taken,
            "reconciled_runs": reconciled_runs,
            "retry_enabled": retry,
            "worktree_conflicts": conflict_report.get("worktree_conflicts", []),
            **cleanup_report,
        }

    def attach_tmux(self, team_id: str) -> int:
        return self.tmux.attach(self.tmux.team_session_name(team_id))
