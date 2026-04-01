from __future__ import annotations

import json
import sys
import shlex
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.agents.codex_adapter import CodexAdapter
from app.config import Settings
from app.core.events import event_payload, event_type_name
from app.db.base import session_scope
from app.db.models import AgentModel, InteractionModel, ReviewRequestModel, RunModel, TaskModel
from app.db.repository import Repository
from app.enums import AgentRole, AgentStatus, EventType, InteractionStatus, ReviewStatus, RunStatus, TaskStatus
from app.logging import get_logger
from app.runtime.output_collector import OutputCollector
from app.schemas import AgentOutput, DispatchResult, ReplanResult, TaskContract
from app.sessions.tmux_manager import TmuxManager, TmuxUnavailableError
from app.workspace.worktree_manager import WorktreeManager


class AlvisServices:
    ACTIVE_TASK_STATUSES = {
        TaskStatus.ASSIGNED.value,
        TaskStatus.RUNNING.value,
        TaskStatus.WAITING_INPUT.value,
    }
    ACTIVE_AGENT_STATUSES = {
        AgentStatus.ASSIGNED.value,
        AgentStatus.RUNNING.value,
        AgentStatus.WAITING_INPUT.value,
    }

    def __init__(self, settings: Settings, session_factory: sessionmaker):
        self.settings = settings
        self.session_factory = session_factory
        self.tmux = TmuxManager(settings.tmux_session_prefix, settings.tmux_path)
        self.codex = CodexAdapter(
            settings.codex_command,
            settings.runtime_dir,
        )
        self.worktrees = WorktreeManager(settings.repo_root, settings.worktree_root)
        self.output_collector = OutputCollector()
        self.log = get_logger(__name__)

    def _parse_worker_role(self, raw_role: str) -> tuple[AgentRole, str]:
        base, _, alias = raw_role.partition(":")
        base_role = AgentRole(base.strip() or AgentRole.IMPLEMENTER.value)
        role_alias = alias.strip() or base_role.value
        return base_role, role_alias

    def create_team(self, team_id: str, worker_1_role: str, worker_2_role: str):
        session_name = self.tmux.team_session_name(team_id)
        worker_roles = [
            self._parse_worker_role(worker_1_role),
            self._parse_worker_role(worker_2_role),
        ]
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            if repo.get_team(team_id) is not None:
                raise ValueError(f"team {team_id} already exists; use `alvis clean` first or choose a new name")
            team = repo.create_team(team_id, session_name, worker_roles)
            repo.append_event(
                team_id=team_id,
                event_type=event_type_name(EventType.TEAM_CREATED),
                payload=event_payload(
                    "Team created",
                    worker_roles=[
                        {"base_role": base_role.value, "role_alias": role_alias}
                        for base_role, role_alias in worker_roles
                    ],
                ),
            )
            return team

    def daemon_health(self) -> dict:
        team_count = len(self.list_workspace_teams())
        return {
            "status": "ok",
            "codex_command": self.settings.codex_command,
            "workspace_root": str(self.settings.repo_root),
            "data_dir": str(self.settings.data_dir),
            "db_path": str(self.settings.db_path),
            "runtime_dir": str(self.settings.runtime_dir),
            "team_count": team_count,
        }

    def _age_seconds(self, value: datetime | None) -> float | None:
        if value is None:
            return None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return max(0.0, (now - value).total_seconds())

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

    def create_task(
        self,
        team_id: str,
        run_id: str,
        title: str,
        goal: str,
        review_required: bool = False,
        target_role_alias: str | None = None,
        owned_paths: list[str] | None = None,
        task_type: str = "worker",
        parent_task_id: str | None = None,
    ):
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.create_task(
                team_id,
                run_id,
                title,
                goal,
                review_required,
                target_role_alias=target_role_alias,
                owned_paths=owned_paths,
                task_type=task_type,
                parent_task_id=parent_task_id,
            )

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
                    elif values["status"] == TaskStatus.WAITING_INPUT.value:
                        agent_status = AgentStatus.WAITING_INPUT.value
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
            return [agent for agent in repo.list_agents(team_id) if agent.role != AgentRole.LEADER.value]

    def _default_task_contract(self, task: TaskModel, agent: AgentModel) -> TaskContract:
        return TaskContract(
            task_id=task.task_id,
            task_type=task.task_type,
            role=agent.role,
            role_alias=agent.role_alias,
            cwd=agent.cwd or str(self.settings.repo_root),
            goal=task.goal,
            owned_paths=task.owned_paths or [],
            constraints=[
                "Do not push changes.",
                "Escalate review before commit.",
                "Only work within the assigned files or paths.",
                "Do not change files outside owned_paths.",
            ],
            expected_output=[
                "Summary",
                "Changed files",
                "Test results",
                "Risks or blockers",
            ],
            coordination_context=[],
            context={"team_id": task.team_id, "run_id": task.run_id},
        )

    def build_task_contract(self, task: TaskModel, agent: AgentModel) -> TaskContract:
        return self._default_task_contract(task, agent)

    def _retry_count(self, repo: Repository, task_id: str) -> int:
        return len(
            [
                event
                for event in repo.list_events()
                if event.task_id == task_id and event.event_type == event_type_name(EventType.TASK_RETRY_REQUESTED)
            ]
        )

    def _source_task_id(self, tasks: list[TaskModel], task: TaskModel) -> str:
        task_map = {candidate.task_id: candidate for candidate in tasks}
        current = task
        while current.title.startswith("Redo:") and current.parent_task_id and current.parent_task_id in task_map:
            current = task_map[current.parent_task_id]
        if current.parent_task_id and current.parent_task_id in task_map and not current.title.startswith("Redo:"):
            return current.parent_task_id
        return current.task_id

    def _redo_attempt_count(self, tasks: list[TaskModel], source_task_id: str) -> int:
        return len(
            [
                candidate
                for candidate in tasks
                if candidate.title.startswith("Redo:") and self._source_task_id(tasks, candidate) == source_task_id
            ]
        )

    def _blocking_conflicts_for_agent(self, team_id: str, agent_id: str) -> list[dict]:
        worktree_report = self.inspect_worktrees(team_id)
        return [
            conflict
            for conflict in worktree_report["scope_conflicts"]
            if any(owner["agent_id"] == agent_id for owner in conflict["owners"])
        ]

    def _paths_overlap(self, left: list[str], right: list[str]) -> bool:
        if not left or not right:
            return False
        for lhs in left:
            lhs_clean = lhs.rstrip("/")
            for rhs in right:
                rhs_clean = rhs.rstrip("/")
                if lhs_clean == rhs_clean:
                    return True
                if lhs_clean.startswith(f"{rhs_clean}/") or rhs_clean.startswith(f"{lhs_clean}/"):
                    return True
        return False

    def runtime_health(self, agent: AgentModel) -> dict:
        pane_alive = bool(agent.tmux_pane) and self.tmux.pane_exists(agent.tmux_pane)
        return self.codex.runtime_health(agent.agent_id, pane_exists=bool(pane_alive))

    def interaction_specs_from_output(self, task: TaskModel, output: AgentOutput) -> list[dict]:
        return self._interaction_specs_from_output(task, output)

    def can_dispatch_task(self, task_id: str, agent_id: str, *, require_live_session: bool = True) -> DispatchResult:
        task = self.get_task(task_id)
        agent = self.get_agent(agent_id)
        if task.target_role_alias and agent.role_alias != task.target_role_alias:
            self.update_task(task_id, status=TaskStatus.BLOCKED.value, result_summary="Dispatch blocked by role mismatch.")
            return DispatchResult(ok=False, reason="role_mismatch")
        if agent.role == AgentRole.IMPLEMENTER.value and not task.owned_paths:
            self.update_task(task_id, status=TaskStatus.BLOCKED.value, result_summary="Dispatch blocked because no file scope was assigned.")
            return DispatchResult(ok=False, reason="missing_owned_paths")
        blocking_conflicts = self._blocking_conflicts_for_agent(agent.team_id, agent_id)
        if blocking_conflicts:
            self.update_task(task_id, status=TaskStatus.BLOCKED.value, result_summary="Dispatch blocked by file scope conflict.")
            self.append_event(
                team_id=agent.team_id,
                run_id=task.run_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type=event_type_name(EventType.WORKTREE_CONFLICT_DETECTED),
                payload=event_payload("Dispatch blocked by file scope conflict", conflicts=blocking_conflicts),
            )
            return DispatchResult(ok=False, reason="scope_conflict")
        if require_live_session and agent.tmux_pane:
            if not self.tmux.pane_exists(agent.tmux_pane):
                self.update_task(task_id, status=TaskStatus.BLOCKED.value, result_summary="Dispatch blocked because tmux pane is unavailable.")
                self.append_event(
                    team_id=agent.team_id,
                    run_id=task.run_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    event_type=event_type_name(EventType.ERROR_RAISED),
                    payload=event_payload("Dispatch blocked because tmux pane is unavailable", reason="pane_unavailable"),
                )
                return DispatchResult(ok=False, reason="pane_unavailable")
            runtime_health = self.codex.runtime_health(agent_id, pane_exists=True)
            if not runtime_health["ready"]:
                reason = runtime_health.get("status") or "session_not_ready"
                self.update_task(task_id, status=TaskStatus.BLOCKED.value, result_summary="Dispatch blocked because the Codex session is not ready.")
                self.append_event(
                    team_id=agent.team_id,
                    run_id=task.run_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    event_type=event_type_name(EventType.ERROR_RAISED),
                    payload=event_payload("Dispatch blocked because the Codex session is not ready", reason=reason),
                )
                return DispatchResult(ok=False, reason=reason)
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
            active_entries = []

            for agent in agents:
                pane_alive = bool(agent.tmux_pane) and self.tmux.pane_exists(agent.tmux_pane)
                current_task = task_map.get(agent.current_task_id) if agent.current_task_id else None
                has_active_task = bool(current_task and current_task.status in self.ACTIVE_TASK_STATUSES)
                entry = {
                    "agent_id": agent.agent_id,
                    "path": str(self.settings.repo_root),
                    "exists": self.settings.repo_root.exists(),
                    "clean": True,
                    "role_alias": agent.role_alias,
                    "owned_paths": current_task.owned_paths if current_task else [],
                    "status": agent.status,
                    "task_id": agent.current_task_id,
                    "pane_alive": pane_alive,
                    "orphaned": (not pane_alive) and agent.status not in self.ACTIVE_AGENT_STATUSES and not has_active_task,
                }
                inspections.append(entry)
                if agent.status in self.ACTIVE_AGENT_STATUSES and entry["owned_paths"]:
                    active_entries.append(entry)
                if entry["orphaned"]:
                    cleanup_candidates.append(entry)

            conflicts = []
            for index, entry in enumerate(active_entries):
                for other in active_entries[index + 1 :]:
                    if not self._paths_overlap(entry["owned_paths"], other["owned_paths"]):
                        continue
                    conflicts.append(
                        {
                            "paths": sorted(set(entry["owned_paths"]) & set(other["owned_paths"])) or [entry["owned_paths"][0]],
                            "owners": [
                                {"agent_id": entry["agent_id"], "task_id": entry["task_id"], "path": entry["path"]},
                                {"agent_id": other["agent_id"], "task_id": other["task_id"], "path": other["path"]},
                            ],
                        }
                    )

            return {
                "workspaces": inspections,
                "cleanup_candidates": cleanup_candidates,
                "scope_conflicts": conflicts,
            }

    def cleanup_worktrees(self, team_id: str | None = None) -> dict:
        teams = [team_id] if team_id else [team.team_id for team in self._list_teams()]
        deleted = []
        skipped_active = []
        for target_team in teams:
            report = self.inspect_worktrees(target_team)
            agent_ids = {item["agent_id"] for item in report["cleanup_candidates"]}
            for entry in report["workspaces"]:
                if entry["agent_id"] in agent_ids:
                    runtime_dir = self.codex.session_paths(entry["agent_id"])["dir"]
                    if runtime_dir.exists():
                        for child in sorted(runtime_dir.rglob("*"), reverse=True):
                            if child.is_file():
                                child.unlink()
                            elif child.is_dir():
                                child.rmdir()
                        if runtime_dir.exists():
                            runtime_dir.rmdir()
                        deleted.append(entry)
                elif entry["status"] in self.ACTIVE_AGENT_STATUSES:
                    skipped_active.append(entry)
        return {
            "deleted_runtime_dirs": deleted,
            "skipped_active_agents": skipped_active,
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

    def list_workspace_teams(self) -> list[dict]:
        teams = []
        for team in self._list_teams():
            teams.append(
                {
                    "team_id": team.team_id,
                    "session_name": team.session_name,
                    "created_at": team.created_at.isoformat() if team.created_at else None,
                }
            )
        teams.sort(key=lambda item: item["created_at"] or "")
        return teams

    def find_attachable_team(self) -> dict | None:
        teams = self.list_workspace_teams()
        if not teams:
            return None
        return teams[-1]

    def _prepare_team_runtime(self, team_id: str) -> None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            agents = repo.list_agents(team_id)
            for agent in agents:
                shared_root, _ = self.worktrees.ensure_worktree(team_id, agent.agent_id)
                repo.update_agent(
                    agent,
                    cwd=str(shared_root),
                    git_branch=None,
                    git_worktree_path=None,
                    tmux_session=None,
                    tmux_window=None,
                    tmux_pane=None,
                    status=AgentStatus.IDLE.value,
                )

    def start_or_attach_default_team(self) -> dict:
        attachable = self.find_attachable_team()
        if attachable:
            return {
                "action": "attached_existing",
                "team_id": attachable["team_id"],
                "session_name": attachable["session_name"],
            }
        team_id = f"team-{uuid.uuid4().hex[:8]}"
        team = self.create_team(team_id, "implementer:executor", "reviewer:reviewer")
        self._prepare_team_runtime(team_id)
        return {
            "action": "created",
            "team_id": team.team_id,
            "session_name": None,
        }

    def clean_workspace_teams(self) -> dict:
        removed = []
        skipped = []
        for team in self.list_workspace_teams():
            result = self.remove_team(team["team_id"])
            if result.get("removed"):
                removed.append(result)
            else:
                skipped.append(result)
        return {
            "removed_teams": removed,
            "skipped_teams": skipped,
            "removed_count": len(removed),
            "skipped_count": len(skipped),
        }

    def dispatch_task(self, agent_id: str, contract: TaskContract) -> DispatchResult:
        agent = self.get_agent(agent_id)
        gate = self.can_dispatch_task(contract.task_id, agent_id, require_live_session=False)
        if not gate.ok:
            return gate
        prompt = self.codex.build_task_prompt(contract)
        if agent.role == AgentRole.LEADER.value:
            return DispatchResult(ok=True, prompt=prompt)
        return self._dispatch_task_inline(agent, contract, prompt, "background_exec")

    def _build_noninteractive_codex_command(self) -> list[str]:
        parts = shlex.split(self.settings.codex_command)
        if not parts:
            return ["codex", "exec", "--color", "never", "-"]
        executable = Path(parts[0]).name
        if executable == "codex" and "exec" not in parts[1:2]:
            return [*parts, "exec", "--color", "never", "-"]
        return parts

    def _task_runner_command(self, paths: dict[str, Path], cwd: str) -> list[str]:
        runner = Path(__file__).resolve().parent / "runtime" / "noninteractive_task_runner.py"
        return [
            sys.executable,
            str(runner),
            "--cwd",
            cwd,
            "--codex-command",
            self.settings.codex_command,
            "--prompt-file",
            str(paths["prompt"]),
            "--heartbeat-file",
            str(paths["heartbeat"]),
            "--stdout-file",
            str(paths["stdout"]),
            "--stderr-file",
            str(paths["stderr"]),
            "--state-file",
            str(paths["state"]),
            "--schema-output-file",
            str(paths["schema_output"]),
            "--last-message-file",
            str(paths["last_message"]),
        ]

    def _codex_output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status_signal": {"type": "string", "enum": sorted(OutputCollector.VALID_STATUS_SIGNALS)},
                "summary": {"type": "string"},
                "question_for_leader": {"type": "array", "items": {"type": "string"}},
                "requested_context": {"type": "array", "items": {"type": "string"}},
                "followup_suggestion": {"type": "array", "items": {"type": "string"}},
                "dependency_note": {"type": "array", "items": {"type": "string"}},
                "changed_files": {"type": "array", "items": {"type": "string"}},
                "test_results": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "status_signal",
                "summary",
                "question_for_leader",
                "requested_context",
                "followup_suggestion",
                "dependency_note",
                "changed_files",
                "test_results",
                "risk_flags",
            ],
            "additionalProperties": False,
        }

    def _build_noninteractive_codex_invocation(self, output_path: Path | None = None, schema_path: Path | None = None) -> list[str]:
        command = self._build_noninteractive_codex_command()
        executable = Path(command[0]).name
        if executable != "codex" or "exec" not in command[1:]:
            return command
        if output_path is None:
            return command
        invocation = list(command)
        stdin_marker = invocation[-1] if invocation[-1] == "-" else None
        if stdin_marker:
            invocation = invocation[:-1]
        if "--skip-git-repo-check" not in invocation:
            invocation.append("--skip-git-repo-check")
        if schema_path is not None and "--output-schema" not in invocation:
            invocation.extend(["--output-schema", str(schema_path)])
        if "--output-last-message" not in invocation and "-o" not in invocation:
            invocation.extend(["-o", str(output_path)])
        if stdin_marker:
            invocation.append(stdin_marker)
        return invocation

    def _run_noninteractive_codex(self, prompt: str, cwd: str) -> tuple[subprocess.CompletedProcess[str], str | None, str | None]:
        with tempfile.TemporaryDirectory(prefix="alvis-codex-") as temp_dir:
            output_path = Path(temp_dir) / "last-message.txt"
            schema_path = Path(temp_dir) / "alvis-output-schema.json"
            schema_output_path = Path(temp_dir) / "alvis-output.json"
            schema_output_text = None
            command = self._build_noninteractive_codex_command()
            executable = Path(command[0]).name if command else ""
            if executable == "codex" and "exec" in command[1:]:
                schema_path.write_text(json.dumps(self._codex_output_schema(), ensure_ascii=False))
                command = self._build_noninteractive_codex_invocation(schema_output_path, schema_path)
            else:
                command = self._build_noninteractive_codex_invocation(output_path, None)
            result = subprocess.run(
                command,
                input=prompt + "\n",
                text=True,
                capture_output=True,
                cwd=cwd,
                check=False,
            )
            if schema_output_path.exists():
                schema_output_text = schema_output_path.read_text()
            final_message = output_path.read_text() if output_path.exists() else None
            return result, final_message, schema_output_text

    def _dispatch_task_inline(self, agent: AgentModel, contract: TaskContract, prompt: str, reason: str) -> DispatchResult:
        paths = self.codex.reset_session_files(agent.agent_id)
        paths["prompt"].write_text(prompt, encoding="utf-8")
        command = self._task_runner_command(paths, contract.cwd)
        try:
            subprocess.Popen(
                command,
                cwd=contract.cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            self.append_event(
                team_id=agent.team_id,
                run_id=contract.context.get("run_id"),
                task_id=contract.task_id,
                agent_id=agent.agent_id,
                event_type=event_type_name(EventType.ERROR_RAISED),
                payload=event_payload("Task launch failed", reason=str(exc)),
            )
            return DispatchResult(ok=False, reason="launch_failed", prompt=prompt)
        self.append_event(
            team_id=agent.team_id,
            run_id=contract.context.get("run_id"),
            task_id=contract.task_id,
            agent_id=agent.agent_id,
            event_type=event_type_name(EventType.AGENT_STATUS_CHANGED),
            payload=event_payload("Agent background task launched", status=AgentStatus.RUNNING.value, reason=reason),
        )
        return DispatchResult(ok=True, reason=reason, prompt=prompt)

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

    def create_interaction(
        self,
        *,
        run_id: str,
        team_id: str,
        kind: str,
        payload: dict,
        source_agent_id: str | None = None,
        target_agent_id: str | None = None,
        target_role_alias: str | None = None,
        task_id: str | None = None,
        status: InteractionStatus = InteractionStatus.PENDING,
    ) -> InteractionModel:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            interaction = repo.create_interaction(
                run_id=run_id,
                team_id=team_id,
                kind=kind,
                payload=payload,
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                target_role_alias=target_role_alias,
                task_id=task_id,
                status=status,
            )
            repo.append_event(
                team_id=team_id,
                run_id=run_id,
                task_id=task_id,
                agent_id=source_agent_id,
                event_type=event_type_name(EventType.INTERACTION_CREATED),
                payload=event_payload(
                    "Interaction created",
                    interaction_id=interaction.interaction_id,
                    interaction_kind=kind,
                    **payload,
                ),
            )
            return interaction

    def list_interactions(
        self,
        *,
        team_id: str | None = None,
        run_id: str | None = None,
        status: InteractionStatus | None = None,
    ) -> list[InteractionModel]:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            return repo.list_interactions(team_id=team_id, run_id=run_id, status=status)

    def summarize_pending_interactions(self, run_id: str) -> list[dict]:
        summaries = []
        for item in self.list_interactions(run_id=run_id, status=InteractionStatus.PENDING):
            payload = item.payload or {}
            message = (
                payload.get("message")
                or payload.get("detail")
                or payload.get("summary")
                or payload.get("output_summary")
                or item.kind
            )
            summaries.append(
                {
                    "interaction_id": item.interaction_id,
                    "task_id": item.task_id,
                    "source_agent_id": item.source_agent_id,
                    "target_agent_id": item.target_agent_id,
                    "target_role_alias": item.target_role_alias,
                    "kind": item.kind,
                    "status": item.status,
                    "message": message,
                    "payload": payload,
                }
            )
        return summaries

    def answer_pending_interaction(self, team_id: str, answer: str) -> dict:
        runs = self.list_team_runs(team_id)
        latest_run = runs[0] if runs else None
        if latest_run is None:
            raise ValueError(f"team {team_id} has no active run")
        pending = [
            item
            for item in self.list_interactions(run_id=latest_run.run_id, status=InteractionStatus.PENDING)
            if item.target_role_alias == "leader" or item.target_agent_id == f"{team_id}-leader"
        ]
        if not pending:
            raise ValueError("no pending leader interaction")
        interaction = pending[0]
        source_task = self.get_task(interaction.task_id) if interaction.task_id else None
        resolved_ids = []
        guidance_payload = {"leader_answer": answer, "leader_summary": answer, "leader_guidance": [answer]}
        for item in pending:
            if source_task and item.task_id != source_task.task_id:
                continue
            self.resolve_interaction(item.interaction_id, payload=guidance_payload)
            resolved_ids.append(item.interaction_id)
        followup_task_id = None
        if source_task is not None:
            followup_goal = (
                f"Continue the original task with leader guidance.\n"
                f"Original goal: {source_task.goal}\n"
                f"Leader guidance: {answer}\n"
            )
            followup_task = self.create_task(
                team_id=source_task.team_id,
                run_id=source_task.run_id,
                title=f"Leader follow-up: {source_task.title}",
                goal=followup_goal,
                review_required=source_task.review_required,
                target_role_alias=source_task.target_role_alias,
                owned_paths=source_task.owned_paths,
                task_type="worker",
                parent_task_id=source_task.task_id,
            )
            worker = next(
                (
                    candidate
                    for candidate in self.list_worker_agents(team_id)
                    if (candidate.role_alias or candidate.role) == source_task.target_role_alias
                ),
                None,
            )
            if worker is not None:
                self.assign_task(followup_task.task_id, worker.agent_id)
                dispatch = self.dispatch_task(worker.agent_id, self._default_task_contract(followup_task, worker))
                self.append_event(
                    team_id=team_id,
                    run_id=source_task.run_id,
                    task_id=followup_task.task_id,
                    agent_id=worker.agent_id,
                    event_type=event_type_name(EventType.LEADER_INSTRUCTION_CREATED),
                    payload=event_payload(
                        "Leader follow-up task created",
                        source_task_id=source_task.task_id,
                        interaction_id=interaction.interaction_id,
                        new_task_id=followup_task.task_id,
                        leader_answer=answer,
                        prompt=dispatch.prompt,
                    ),
                )
                followup_task_id = followup_task.task_id
            self.update_task(
                source_task.task_id,
                status=TaskStatus.CANCELLED.value,
                result_summary=f"Superseded by leader guidance: {answer}",
            )
        return {
            "run_id": latest_run.run_id,
            "interaction_id": interaction.interaction_id,
            "resolved_interaction_ids": resolved_ids,
            "followup_task_id": followup_task_id,
            "leader_answer": answer,
        }

    def resolve_interaction(self, interaction_id: str, *, payload: dict | None = None) -> InteractionModel | None:
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            interaction = repo.get_interaction(interaction_id)
            if not interaction:
                return None
            if payload:
                interaction.payload = {**interaction.payload, **payload}
            resolved = repo.resolve_interaction(interaction)
            repo.append_event(
                team_id=resolved.team_id,
                run_id=resolved.run_id,
                task_id=resolved.task_id,
                agent_id=resolved.source_agent_id,
                event_type=event_type_name(EventType.INTERACTION_RESOLVED),
                payload=event_payload(
                    "Interaction resolved",
                    interaction_id=resolved.interaction_id,
                    interaction_kind=resolved.kind,
                ),
            )
            return resolved

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
        workers = [agent for agent in repo.list_agents(team_id) if agent.role != AgentRole.LEADER.value]
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
            target_role_alias=replan_agent.role_alias,
            owned_paths=task.owned_paths,
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
                result_summary="Dispatch blocked by file scope conflict.",
            )
            repo.append_event(
                team_id=task.team_id,
                run_id=run.run_id,
                task_id=new_task.task_id,
                agent_id=replan_agent.agent_id,
                event_type=event_type_name(EventType.WORKTREE_CONFLICT_DETECTED),
                payload=event_payload("Dispatch blocked by file scope conflict", conflicts=blocking_conflicts),
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
            role_alias=replan_agent.role_alias,
            cwd=replan_agent.cwd or str(self.settings.repo_root),
            goal=new_task.goal,
            owned_paths=new_task.owned_paths,
            constraints=[
                "This task was created because review was rejected.",
                "Address the rejection reason directly.",
                "Do not push changes.",
                "Escalate review before commit.",
                "Only work within the assigned files or paths.",
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
            runtime_health = self.codex.runtime_health(
                replan_agent.agent_id,
                pane_exists=self.tmux.pane_exists(replan_agent.tmux_pane),
            )
            if not runtime_health["ready"]:
                repo.update_task(
                    new_task,
                    status=TaskStatus.BLOCKED.value,
                    result_summary="Dispatch blocked because the Codex session is not ready.",
                )
                repo.update_agent(replan_agent, status=AgentStatus.BLOCKED.value)
                return ReplanResult(
                    review_id=review.review_id,
                    parent_task_id=task.task_id,
                    new_task_id=new_task.task_id,
                    assigned_agent_id=replan_agent.agent_id,
                    reason=reason,
                )
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

    def _interaction_specs_from_output(self, task: TaskModel, output: AgentOutput) -> list[dict]:
        specs: list[dict] = []
        for question in output.question_for_leader:
            specs.append({"kind": "request_input", "message": question})
        for context in output.requested_context:
            specs.append({"kind": "request_input", "message": context})
        for suggestion in output.followup_suggestion:
            specs.append({"kind": "leader_instruction", "message": suggestion})
        for dependency in output.dependency_note:
            specs.append({"kind": "share_context", "message": dependency})
        if output.status_signal == "blocked":
            specs.append({"kind": "report_blocker", "message": output.summary})
        elif output.status_signal == "needs_review":
            specs.append({"kind": "request_review", "message": output.summary})
        return [
            {
                **spec,
                "task_id": task.task_id,
                "target_role_alias": "leader",
                "source_task_title": task.title,
            }
            for spec in specs
        ]

    def _background_failure_details(self, agent_id: str, *, exit_code: int | None, stderr_text: str) -> dict[str, str | int | None]:
        stderr_summary = self.codex.stderr_summary(agent_id)
        summary = str(
            stderr_summary.get("summary")
            or (stderr_text.splitlines()[-1].strip() if stderr_text.strip() else "")
            or f"Codex 실행이 exit code {exit_code}로 종료되었습니다."
        ).strip()
        hint = str(stderr_summary.get("hint") or "").strip() or None
        last_line = str(stderr_summary.get("last_line") or "").strip() or None
        detail = last_line or (stderr_text.splitlines()[-1].strip() if stderr_text.strip() else None)
        return {
            "summary": summary,
            "hint": hint,
            "last_line": last_line,
            "detail": detail,
            "exit_code": exit_code,
        }

    def refresh_agent_runtime(self, agent_id: str) -> AgentOutput | None:
        agent = self.get_agent(agent_id)
        paths = self.codex.session_paths(agent_id)
        state = self.codex.read_session_state(agent_id)
        snapshot = self.output_collector.read_snapshot(paths["stdout"], paths["heartbeat"])
        if snapshot.heartbeat_at:
            self.record_heartbeat(agent_id, snapshot.heartbeat_at)
        task_id = agent.current_task_id
        if not task_id:
            return None
        if state.get("status") != "exited":
            return None
        if state.get("output_collected"):
            return None
        output = self.output_collector.summarize_task_output(
            agent_id=agent_id,
            task_id=task_id,
            log_text=snapshot.log_text,
            final_message_text=paths["last_message"].read_text(encoding="utf-8") if paths["last_message"].exists() else None,
            schema_output_text=paths["schema_output"].read_text(encoding="utf-8") if paths["schema_output"].exists() else None,
        )
        exit_code = state.get("exit_code")
        if output.kind == "delta":
            if exit_code == 0:
                parse_status = output.output_parse_status or OutputCollector.PARSE_NO_RESULT_BLOCK
                parse_reason = "Missing ALVIS structured result block."
                if parse_status == OutputCollector.PARSE_INVALID_RESULT_BLOCK:
                    parse_reason = "Invalid ALVIS structured result block."
                if parse_status == OutputCollector.PARSE_SCHEMA_PARSE_FAILED:
                    parse_reason = "Structured Codex output could not be parsed as JSON."
                if parse_status == OutputCollector.PARSE_SCHEMA_CONTRACT_FAILED:
                    parse_reason = "Structured Codex output did not match the expected contract."
                output = AgentOutput(
                    task_id=task_id,
                    agent_id=agent_id,
                    kind="final",
                    status_signal="blocked",
                    summary="Task did not produce a valid structured result block.",
                    output_parse_status=parse_status,
                    changed_files=output.changed_files,
                    test_results=output.test_results,
                    risk_flags=output.risk_flags or [parse_reason],
                )
            else:
                stderr_text = paths["stderr"].read_text(encoding="utf-8", errors="ignore").strip() if paths["stderr"].exists() else ""
                failure = self._background_failure_details(agent_id, exit_code=exit_code, stderr_text=stderr_text)
                risk_flags = []
                if failure["detail"]:
                    risk_flags.append(str(failure["detail"]))
                elif stderr_text:
                    risk_flags.append(stderr_text)
                if failure["hint"]:
                    risk_flags.append(str(failure["hint"]))
                output = AgentOutput(
                    task_id=task_id,
                    agent_id=agent_id,
                    kind="final",
                    summary=str(failure["summary"]),
                    status_signal="blocked",
                    output_parse_status=None,
                    risk_flags=risk_flags or ["background execution failed"],
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
        state["output_collected"] = True
        paths["state"].write_text(json.dumps(state), encoding="utf-8")
        task = self.get_task(task_id)
        existing = {
            (item.kind, item.payload.get("message"))
            for item in self.list_interactions(run_id=task.run_id, status=InteractionStatus.PENDING)
            if item.task_id == task_id
        }
        for spec in self._interaction_specs_from_output(task, output):
            key = (spec["kind"], spec.get("message"))
            if key in existing:
                continue
            self.create_interaction(
                run_id=task.run_id,
                team_id=task.team_id,
                kind=spec["kind"],
                payload=spec,
                source_agent_id=agent_id,
                target_role_alias=spec.get("target_role_alias"),
                task_id=task_id,
            )
        if exit_code not in (None, 0) or output.status_signal == "blocked":
            failure = self._background_failure_details(
                agent_id,
                exit_code=exit_code,
                stderr_text=paths["stderr"].read_text(encoding="utf-8", errors="ignore").strip() if paths["stderr"].exists() else "",
            )
            self.append_event(
                team_id=agent.team_id,
                run_id=task.run_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type=event_type_name(EventType.ERROR_RAISED),
                payload=event_payload(
                    "Task execution via background runner needs attention",
                    exit_code=exit_code,
                    reason=state.get("reason") or "background_exec",
                    error_summary=failure["summary"],
                    error_hint=failure["hint"],
                    detail=failure["detail"],
                ),
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
        missing_runtime_state = []
        runtime_not_ready = []
        exited_runners = []
        uncollected_outputs = []
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
            stale_agent_ids = {agent.agent_id for agent in repo.find_stalled_agents(self.settings.heartbeat_timeout_seconds)}
            agent_by_id = {agent.agent_id: agent for agent in agents}

            for agent in agents:
                has_active_assignment = bool(agent.current_task_id or agent.status in self.ACTIVE_AGENT_STATUSES)
                health = self.runtime_health(agent)
                state = self.codex.read_session_state(agent.agent_id)
                if not has_active_assignment:
                    if agent.agent_id in stale_agent_ids:
                        stale_heartbeat.append(agent.agent_id)
                    continue
                if health["status"] in {"not_ready", "missing_pane"}:
                    missing_runtime_state.append(agent.agent_id)
                elif health["status"] == "starting":
                    runtime_not_ready.append(agent.agent_id)
                elif health["status"] == "exited":
                    exited_runners.append(agent.agent_id)
                    if state.get("output_collected") is not True:
                        uncollected_outputs.append(agent.agent_id)
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
                health = self.runtime_health(agent)
                if health["status"] in {"not_ready", "missing_pane", "starting"}:
                    orphaned_tasks.append(task.task_id)
                    continue
                if health["status"] == "exited" and self.codex.read_session_state(agent.agent_id).get("output_collected") is True:
                    orphaned_tasks.append(task.task_id)
                    continue
                if agent.agent_id in stale_agent_ids:
                    orphaned_tasks.append(task.task_id)

            for run in runs:
                if run.status not in {RunStatus.RUNNING.value}:
                    continue
                run_tasks = [task for task in tasks if task.run_id == run.run_id]
                has_active_tasks = any(task.status in self.ACTIVE_TASK_STATUSES for task in run_tasks)
                if not has_active_tasks:
                    dangling_runs.append(run.run_id)

        return {
            "missing_runtime_state": sorted(set(missing_runtime_state)),
            "stale_heartbeat": sorted(set(stale_heartbeat)),
            "runtime_not_ready": sorted(set(runtime_not_ready)),
            "exited_runners": sorted(set(exited_runners)),
            "uncollected_outputs": sorted(set(uncollected_outputs)),
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
            if not agent or not self.runtime_health(agent)["ready"]:
                repo.append_event(
                    team_id=task.team_id,
                    run_id=task.run_id,
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    event_type=event_type_name(EventType.TASK_RETRY_SKIPPED),
                    payload=event_payload("Retry skipped", reason="runtime unavailable"),
                )
                actions_taken.append({"type": "retry_skipped", "task_id": task.task_id, "reason": "runtime unavailable"})
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
                    payload=event_payload("Retry skipped", reason="scope conflict"),
                )
                actions_taken.append({"type": "retry_skipped", "task_id": task.task_id, "reason": "scope conflict"})
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
            interactions = repo.list_interactions(run_id=latest_run.run_id) if latest_run else []
            checkpoint = repo.get_checkpoint(latest_run.run_id) if latest_run else None
            workspace_report = self.inspect_worktrees(team_id)
            final_output_candidate = None
            final_output_ready = False
            if latest_run:
                reviewer_present = any(agent.role == AgentRole.REVIEWER.value for agent in agents)
                completed_tasks = [task for task in tasks if task.status == TaskStatus.DONE.value]
                for candidate_task in reversed(completed_tasks):
                    output = self.get_task_output(candidate_task.task_id)
                    if output and output.kind == "final":
                        candidate_ready = (output.status_signal or "done") == "done" and (
                            candidate_task.parent_task_id is not None or not reviewer_present
                        )
                        final_output_candidate = {
                            "task_id": candidate_task.task_id,
                            "agent_id": candidate_task.agent_id,
                            "summary": output.summary,
                            "status_signal": output.status_signal,
                            "changed_files": output.changed_files,
                            "test_results": output.test_results,
                            "risk_flags": output.risk_flags,
                        }
                        final_output_ready = candidate_ready
                        break
            retry_candidates = []
            session_errors = []
            for task in tasks:
                if task.status != TaskStatus.BLOCKED.value or not task.agent_id:
                    continue
                agent = next((item for item in agents if item.agent_id == task.agent_id), None)
                if not agent:
                    continue
                if not self.runtime_health(agent)["ready"]:
                    continue
                if self._retry_count(repo, task.task_id) >= self.settings.review_retry_threshold:
                    continue
                if self._blocking_conflicts_for_agent(team_id, agent.agent_id):
                    continue
                retry_candidates.append({"task_id": task.task_id, "agent_id": agent.agent_id})
            agent_payloads = []
            for agent in agents:
                runtime_health = self.runtime_health(agent)
                agent_payload = {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "role_alias": agent.role_alias,
                    "status": agent.status,
                    "pane": agent.tmux_pane,
                    "cwd": agent.cwd,
                    "task": agent.current_task_id,
                    "last_heartbeat_at": None if not agent.last_heartbeat_at else agent.last_heartbeat_at.isoformat(),
                    "runtime_health": runtime_health,
                    "runtime_status": runtime_health.get("status"),
                    "pid": runtime_health.get("pid"),
                    "exit_code": runtime_health.get("exit_code"),
                }
                agent_payloads.append(agent_payload)
                if runtime_health.get("error_summary"):
                    session_errors.append(
                        {
                            "agent_id": agent.agent_id,
                            "runtime_status": runtime_health.get("status"),
                            "error_summary": runtime_health.get("error_summary"),
                            "error_hint": runtime_health.get("error_hint"),
                        }
                    )
            pending_interactions = [
                {
                    "interaction_id": item.interaction_id,
                    "task_id": item.task_id,
                    "source_agent_id": item.source_agent_id,
                    "target_agent_id": item.target_agent_id,
                    "target_role_alias": item.target_role_alias,
                    "kind": item.kind,
                    "status": item.status,
                    "message": (item.payload or {}).get("message")
                    or (item.payload or {}).get("detail")
                    or (item.payload or {}).get("summary"),
                    "payload": item.payload,
                }
                for item in interactions
                if item.status == InteractionStatus.PENDING.value
            ]
            execution_summary = {
                "dispatching_tasks": len(
                    [task for task in tasks if task.status in {TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value}]
                ),
                "waiting_interactions": len(pending_interactions),
                "blocked_tasks": len([task for task in tasks if task.status == TaskStatus.BLOCKED.value]),
                "last_important_event": None,
                "run_age_seconds": self._age_seconds(latest_run.created_at) if latest_run else None,
                "latest_task_update_age_seconds": min(
                    (self._age_seconds(task.updated_at) for task in tasks if task.updated_at),
                    default=None,
                ),
                "oldest_pending_interaction_age_seconds": min(
                    (self._age_seconds(item.created_at) for item in interactions if item.status == InteractionStatus.PENDING.value),
                    default=None,
                ),
            }
            for event in reversed(repo.list_events(team_id=team_id, run_id=latest_run.run_id) if latest_run else []):
                if event.event_type in {
                    event_type_name(EventType.TASK_ASSIGNED),
                    event_type_name(EventType.AGENT_OUTPUT_FINAL),
                    event_type_name(EventType.INTERACTION_CREATED),
                    event_type_name(EventType.INTERACTION_RESOLVED),
                    event_type_name(EventType.LEADER_INSTRUCTION_CREATED),
                    event_type_name(EventType.ERROR_RAISED),
                    event_type_name(EventType.LEADER_OUTPUT_READY),
                }:
                    execution_summary["last_important_event"] = event.payload.get("summary") or event.event_type
                    break
            return {
                "team_id": team.team_id,
                "session_name": team.session_name,
                "agents": agent_payloads,
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
                "execution_summary": execution_summary,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "source_task_id": self._source_task_id(tasks, task),
                        "task_type": task.task_type,
                        "parent_task_id": task.parent_task_id,
                        "title": task.title,
                        "goal": task.goal,
                        "target_role_alias": task.target_role_alias,
                        "owned_paths": task.owned_paths,
                        "status": task.status,
                        "agent_id": task.agent_id,
                        "redo_attempt_count": self._redo_attempt_count(tasks, self._source_task_id(tasks, task)),
                        "redo_limit_reached": self._redo_attempt_count(tasks, self._source_task_id(tasks, task))
                        >= self.settings.redo_attempt_limit,
                        "result_summary": task.result_summary,
                        "latest_output": None
                        if not self.get_task_output(task.task_id)
                        else self.get_task_output(task.task_id).model_dump(),
                    }
                    for task in tasks
                ],
                "pending_reviews": [],
                "handoffs": [
                    {
                        "task_id": task.task_id,
                        "parent_task_id": task.parent_task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "status": task.status,
                        "target_role_alias": task.target_role_alias,
                    }
                    for task in tasks
                    if task.parent_task_id
                ],
                "final_output_candidate": final_output_candidate,
                "final_output_ready": final_output_ready,
                "redo_tasks": [
                    {
                        "task_id": task.task_id,
                        "parent_task_id": task.parent_task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "status": task.status,
                        "target_role_alias": task.target_role_alias,
                    }
                    for task in tasks
                    if task.title.startswith("Redo:")
                ],
                "pending_interactions": pending_interactions,
                "leader_queue": [
                    {
                        "interaction_id": item.interaction_id,
                        "kind": item.kind,
                        "message": item.payload.get("message"),
                        "task_id": item.task_id,
                    }
                    for item in interactions
                    if item.status == InteractionStatus.PENDING.value and (item.target_role_alias == "leader" or item.target_agent_id == f"{team_id}-leader")
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
                "scope_conflicts": workspace_report["scope_conflicts"],
                "cleanup_candidates": workspace_report["cleanup_candidates"],
                "retry_candidates": retry_candidates,
                "session_errors": session_errors,
            }

    def recover(self, team_id: str | None = None, retry: bool = False) -> dict:
        inspection = self.inspect_runtime_state(team_id)
        actions_taken: list[dict] = []
        reconciled_runs: list[str] = []
        collected_outputs: list[str] = []

        if team_id:
            for agent_id in inspection["uncollected_outputs"]:
                output = self.refresh_agent_runtime(agent_id)
                if output is not None:
                    collected_outputs.append(agent_id)
                    actions_taken.append({"type": "output_collected", "agent_id": agent_id, "task_id": output.task_id})
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

            for agent_id in inspection["missing_runtime_state"]:
                agent = agent_map.get(agent_id)
                if not agent:
                    continue
                repo.update_agent(agent, status=AgentStatus.BLOCKED.value)
                actions_taken.append({"type": "agent_blocked_missing_runtime", "agent_id": agent_id})
                if agent.current_task_id and agent.current_task_id in task_map:
                    repo.update_task(task_map[agent.current_task_id], status=TaskStatus.BLOCKED.value)
                    actions_taken.append({"type": "task_blocked_missing_runtime", "task_id": agent.current_task_id})

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
            }
        session_errors = []
        if team_id:
            for agent in self.list_worker_agents(team_id):
                runtime_health = self.runtime_health(agent)
                if runtime_health.get("error_summary"):
                    session_errors.append(
                        {
                            "agent_id": agent.agent_id,
                            "runtime_status": runtime_health.get("status"),
                            "error_summary": runtime_health.get("error_summary"),
                            "error_hint": runtime_health.get("error_hint"),
                        }
                    )
            try:
                leader = self.get_agent(f"{team_id}-leader")
                runtime_health = self.runtime_health(leader)
                if runtime_health.get("error_summary"):
                    session_errors.append(
                        {
                            "agent_id": leader.agent_id,
                            "runtime_status": runtime_health.get("status"),
                            "error_summary": runtime_health.get("error_summary"),
                            "error_hint": runtime_health.get("error_hint"),
                        }
                    )
            except ValueError:
                pass

        return {
            **inspection,
            "actions_taken": actions_taken,
            "collected_outputs": collected_outputs,
            "reconciled_runs": reconciled_runs,
            "retry_enabled": retry,
            "scope_conflicts": conflict_report.get("scope_conflicts", []),
            "session_errors": session_errors,
            **cleanup_report,
        }

    def remove_team(self, team_id: str) -> dict:
        session_name = self.tmux.team_session_name(team_id)
        try:
            self.tmux.kill_session(session_name)
        except TmuxUnavailableError:
            self.log.warning("team.remove.tmux_unavailable", team_id=team_id, session_name=session_name)
        runtime_dir = self.codex.runtime_dir / "agents"
        removed_agent_dirs = []
        with session_scope(self.session_factory) as session:
            repo = Repository(session)
            agents = repo.list_agents(team_id)
            for agent in agents:
                agent_dir = self.codex.session_paths(agent.agent_id)["dir"]
                if agent_dir.exists():
                    for child in sorted(agent_dir.rglob("*"), reverse=True):
                        if child.is_file():
                            child.unlink()
                        elif child.is_dir():
                            child.rmdir()
                    if agent_dir.exists():
                        agent_dir.rmdir()
                    removed_agent_dirs.append(agent.agent_id)
            removed = repo.delete_team(team_id)
        if runtime_dir.exists() and not any(runtime_dir.iterdir()):
            runtime_dir.rmdir()
        return {
            "team_id": team_id,
            "removed": removed,
            "removed_agent_runtime_dirs": removed_agent_dirs,
            "session_name": session_name,
        }
