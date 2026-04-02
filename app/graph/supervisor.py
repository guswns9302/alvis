from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Any

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover
    END = "__end__"
    StateGraph = None

from app.core.events import event_payload, event_type_name
from app.enums import AgentRole, EventType, InteractionStatus, RunStatus, TaskStatus
from app.graph.state import AlvisRunState
from app.logging import get_logger
from app.schemas import TaskContract


@dataclass
class SupervisorDeps:
    services: Any


class Supervisor:
    NODE_ORDER = {
        "ingest_request": "plan_tasks",
        "plan_tasks": "select_agents",
        "select_agents": "dispatch_tasks",
        "dispatch_tasks": "wait_for_updates",
        "wait_for_updates": "evaluate_progress",
        "evaluate_progress": "route_interactions",
        "route_interactions": "synthesize_result",
    }

    def __init__(self, deps: SupervisorDeps):
        self.deps = deps
        self.log = get_logger(__name__)
        self._compiled_graphs: dict[str, Any] = {}

    def _graph_invoke_config(self) -> dict[str, Any]:
        return {"recursion_limit": self.deps.services.settings.graph_recursion_limit}

    def _invoke_graph(self, graph: Any, state: AlvisRunState):
        try:
            return graph.invoke(state, self._graph_invoke_config())
        except TypeError:
            return graph.invoke(state)

    def _extract_paths(self, request: str) -> list[str]:
        tokens = [token.strip("`'\",.") for token in request.split()]
        paths = []
        for token in tokens:
            if "/" in token or "." in token:
                if token and token not in paths:
                    paths.append(token)
        return paths or ["."]

    def _plan_template(self, request: str) -> tuple[str, str, str]:
        lowered = request.lower()
        if any(keyword in lowered for keyword in ("compare", "versus", " vs ", "analysis", "analyze", "report", "비교", "차이", "분석", "보고서")):
            return (
                "Research and draft findings",
                f"Research the request, compare the relevant options, and draft findings for: {request}",
                "Validate the findings, identify weak claims, and prepare the final answer.",
            )
        if any(keyword in lowered for keyword in ("review", "audit", "검토", "리뷰", "감사")):
            return (
                "Inspect and assess work",
                f"Inspect the target subject, identify risks or issues, and draft an assessment for: {request}",
                "Validate the assessment, confirm important findings, and prepare the final verdict.",
            )
        return (
            "Implement changes",
            f"Implement the requested work for: {request}",
            "Validate the implementation output and prepare the final answer.",
        )

    def create_plan(self, request: str, workers: list[dict[str, str]]) -> list[dict[str, Any]]:
        primary = next((worker for worker in workers if worker["role"] != "reviewer"), workers[0])
        primary_title, primary_goal, reviewer_goal = self._plan_template(request)
        plan = [
            {
                "title": primary_title,
                "goal": primary_goal,
                "target_role_alias": primary["role_alias"],
                "owned_paths": self._extract_paths(request),
                "review_required": False,
            }
        ]
        reviewer = next((worker for worker in workers if worker["role"] == "reviewer"), None)
        if reviewer is not None:
            plan.append(
                {
                    "title": "Validate and summarize",
                    "goal": f"{reviewer_goal}\nOriginal request: {request}",
                    "target_role_alias": reviewer["role_alias"],
                    "owned_paths": [],
                    "review_required": False,
                    "parent_index": 0,
                }
            )
        return plan

    def build_graph(self, entry_point: str = "ingest_request"):
        if StateGraph is None:
            return None
        if entry_point in self._compiled_graphs:
            return self._compiled_graphs[entry_point]
        graph = StateGraph(AlvisRunState)
        graph.add_node("ingest_request", self.ingest_request)
        graph.add_node("plan_tasks", self.plan_tasks)
        graph.add_node("select_agents", self.select_agents)
        graph.add_node("dispatch_tasks", self.dispatch_tasks)
        graph.add_node("wait_for_updates", self.wait_for_updates)
        graph.add_node("evaluate_progress", self.evaluate_progress)
        graph.add_node("route_interactions", self.route_interactions)
        graph.add_node("synthesize_result", self.synthesize_result)
        graph.add_edge("ingest_request", "plan_tasks")
        graph.add_edge("plan_tasks", "select_agents")
        graph.add_edge("select_agents", "dispatch_tasks")
        graph.add_edge("dispatch_tasks", "wait_for_updates")
        graph.add_edge("wait_for_updates", "evaluate_progress")
        graph.add_conditional_edges(
            "evaluate_progress",
            self._route_after_evaluate_progress,
            {
                "wait_for_updates": "wait_for_updates",
                "route_interactions": "route_interactions",
            },
        )
        graph.add_conditional_edges(
            "route_interactions",
            self._route_after_interactions,
            {
                "wait_for_updates": "wait_for_updates",
                "synthesize_result": "synthesize_result",
            },
        )
        graph.add_edge("synthesize_result", END)
        graph.set_entry_point(entry_point)
        compiled = graph.compile()
        self._compiled_graphs[entry_point] = compiled
        return compiled

    def run(self, team_id: str, request: str) -> dict[str, Any]:
        state = AlvisRunState(
            team_id=team_id,
            user_request=request,
            tasks=[],
            assignments=[],
            active_tasks=[],
            completed_tasks=[],
            blocked_tasks=[],
            review_requests=[],
            pending_interactions=[],
            handoffs=[],
            final_output_candidate=None,
            final_output_ready=False,
            status=RunStatus.CREATED.value,
        )
        graph = self.build_graph("ingest_request")
        if graph is None:
            return self._execute_from_node(state, "ingest_request")
        return self._invoke_graph(graph, state)

    def resume(self, run_id: str) -> AlvisRunState:
        checkpoint = self.deps.services.load_checkpoint(run_id)
        if checkpoint is None:
            raise ValueError(f"run {run_id} has no checkpoint to resume")
        state = AlvisRunState(**checkpoint.state)
        graph = self.build_graph(checkpoint.next_node)
        if graph is None:
            state = self._refresh_state_from_db(state)
            return self._execute_from_node(state, checkpoint.next_node)
        return self._invoke_graph(graph, state)

    def _refresh_state_from_db(self, state: AlvisRunState) -> AlvisRunState:
        if "run_id" not in state:
            return state
        tasks = self.deps.services.list_run_tasks(state["run_id"])
        state["final_output_candidate"] = None
        state["final_output_ready"] = False
        state["tasks"] = [
            {
                "task_id": task.task_id,
                "agent_id": task.agent_id,
                "task_type": task.task_type,
                "parent_task_id": task.parent_task_id,
                "title": task.title,
                "goal": task.goal,
                "status": task.status,
                "review_required": task.review_required,
                "target_role_alias": task.target_role_alias,
                "owned_paths": task.owned_paths,
            }
            for task in tasks
        ]
        state["review_requests"] = []
        state["pending_interactions"] = [
            {
                "interaction_id": item.interaction_id,
                "task_id": item.task_id,
                "source_agent_id": item.source_agent_id,
                "target_agent_id": item.target_agent_id,
                "target_role_alias": item.target_role_alias,
                "kind": item.kind,
                "status": item.status,
                "payload": item.payload,
            }
            for item in self.deps.services.list_interactions(run_id=state["run_id"])
            if item.status == "pending"
        ]
        state["handoffs"] = [
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
        ]
        completed = [task for task in tasks if task.status == TaskStatus.DONE.value]
        for candidate_task in reversed(completed):
            output = self.deps.services.get_task_output(candidate_task.task_id)
            if output and output.kind == "final":
                state["final_output_candidate"] = {
                    "task_id": candidate_task.task_id,
                    "agent_id": candidate_task.agent_id,
                    "summary": output.summary,
                    "changed_files": output.changed_files,
                    "risk_flags": output.risk_flags,
                    "status_signal": output.status_signal,
                }
                state["final_output_ready"] = (output.status_signal or "done") == "done"
                break
        return state

    def _save_checkpoint(self, state: AlvisRunState, next_node: str) -> None:
        if "run_id" not in state:
            return
        self.deps.services.save_checkpoint(
            run_id=state["run_id"],
            thread_id=state["run_id"],
            next_node=next_node,
            state=dict(state),
        )

    def _save_linear_checkpoint(self, state: AlvisRunState, current: str) -> None:
        next_node = self.NODE_ORDER.get(current)
        if next_node:
            self._save_checkpoint(state, next_node)

    def _route_after_evaluate_progress(self, state: AlvisRunState) -> str:
        next_node = "wait_for_updates" if not state.get("pending_interactions") and state["status"] == RunStatus.RUNNING.value else "route_interactions"
        self._save_checkpoint(state, next_node)
        return next_node

    def _route_after_interactions(self, state: AlvisRunState) -> str:
        if state.get("pending_interactions"):
            self._save_checkpoint(state, "route_interactions")
            return "synthesize_result"
        next_node = "wait_for_updates" if state["status"] == RunStatus.RUNNING.value else "synthesize_result"
        self._save_checkpoint(state, next_node)
        return next_node

    def _execute_from_node(self, state: AlvisRunState, node_name: str) -> AlvisRunState:
        current = node_name
        while True:
            if current in {"wait_for_updates", "evaluate_progress", "route_interactions", "synthesize_result"}:
                state = self._refresh_state_from_db(state)
            state = getattr(self, current)(state)
            next_node = self._determine_next_node(current, state)
            if next_node is None:
                if "run_id" in state:
                    self.deps.services.clear_checkpoint(state["run_id"])
                return state
            self._save_checkpoint(state, next_node)
            current = next_node

    def _determine_next_node(self, current: str, state: AlvisRunState) -> str | None:
        if current == "evaluate_progress":
            if state.get("pending_interactions"):
                return "route_interactions"
            if state["status"] == RunStatus.RUNNING.value:
                return "wait_for_updates"
            return "route_interactions"
        if current == "route_interactions":
            if state["status"] == RunStatus.RUNNING.value:
                return "wait_for_updates"
            return "synthesize_result"
        if current == "synthesize_result":
            return None
        return self.NODE_ORDER[current]

    def _reviewer_for_team(self, team_id: str):
        return next(
            (agent for agent in self.deps.services.list_worker_agents(team_id) if agent.role == AgentRole.REVIEWER.value),
            None,
        )

    def _existing_handoff(self, parent_task_id: str) -> bool:
        return any(task.parent_task_id == parent_task_id for task in self.deps.services.list_run_tasks(self.deps.services.get_task(parent_task_id).run_id))

    def _existing_child_task(self, task_id: str, *, title_prefix: str | None = None) -> bool:
        source = self.deps.services.get_task(task_id)
        for candidate in self.deps.services.list_run_tasks(source.run_id):
            if candidate.parent_task_id != task_id:
                continue
            if title_prefix and not candidate.title.startswith(title_prefix):
                continue
            return True
        return False

    def _child_tasks(self, parent_task_id: str):
        source = self.deps.services.get_task(parent_task_id)
        return [candidate for candidate in self.deps.services.list_run_tasks(source.run_id) if candidate.parent_task_id == parent_task_id]

    def _pending_child_task(self, parent_task_id: str, *, title_prefix: str | None = None):
        for candidate in self._child_tasks(parent_task_id):
            if title_prefix and not candidate.title.startswith(title_prefix):
                continue
            if candidate.agent_id:
                continue
            if candidate.status != TaskStatus.CREATED.value:
                continue
            return candidate
        return None

    def _redo_source_task(self, task):
        current = task
        while current.title.startswith("Redo:") and current.parent_task_id:
            current = self.deps.services.get_task(current.parent_task_id)
        if current.parent_task_id and not current.title.startswith("Redo:"):
            return self.deps.services.get_task(current.parent_task_id)
        return current

    def _redo_attempt_count(self, task) -> int:
        source_task = self._redo_source_task(task)
        return sum(
            1
            for candidate in self.deps.services.list_run_tasks(source_task.run_id)
            if candidate.title.startswith("Redo:") and self._redo_source_task(candidate).task_id == source_task.task_id
        )

    def _create_reviewer_handoff(self, state: AlvisRunState, task, output, summary: str):
        reviewer = self._reviewer_for_team(state["team_id"])
        if reviewer is None or self._existing_handoff(task.task_id):
            return None
        handoff_goal = (
            f"Validate the previous worker output and prepare the final answer.\n"
            f"Original request: {state['user_request']}\n"
            f"Source task: {task.title}\n"
            f"Source summary: {summary}\n"
        )
        handoff_task = self.deps.services.create_task(
            team_id=task.team_id,
            run_id=task.run_id,
            title="Validate and summarize",
            goal=handoff_goal,
            review_required=False,
            target_role_alias=reviewer.role_alias,
            owned_paths=[],
            task_type="worker",
            parent_task_id=task.task_id,
        )
        self.deps.services.append_event(
            team_id=state["team_id"],
            run_id=state["run_id"],
            task_id=handoff_task.task_id,
            agent_id=reviewer.agent_id,
            event_type=event_type_name(EventType.TASK_HANDOFF_CREATED),
            payload=event_payload("Worker handoff created", source_task_id=task.task_id, target_agent_id=reviewer.agent_id),
        )
        self.deps.services.assign_task(handoff_task.task_id, reviewer.agent_id)
        dispatch = self.deps.services.dispatch_task(reviewer.agent_id, self.deps.services.build_task_contract(handoff_task, reviewer))
        self.deps.services.append_event(
            team_id=state["team_id"],
            run_id=state["run_id"],
            task_id=handoff_task.task_id,
            agent_id=reviewer.agent_id,
            event_type=event_type_name(EventType.TASK_HANDOFF_DISPATCHED),
            payload=event_payload("Worker handoff dispatched", source_task_id=task.task_id, prompt=dispatch.prompt),
        )
        return handoff_task

    def _dispatch_child_task(self, state: AlvisRunState, parent_task, child_task, summary: str):
        worker = next(
            (
                candidate
                for candidate in self.deps.services.list_worker_agents(state["team_id"])
                if (candidate.role_alias or candidate.role) == child_task.target_role_alias
            ),
            None,
        )
        if worker is None:
            return None
        updated_goal = (
            f"{child_task.goal}\n"
            f"Source task: {parent_task.title}\n"
            f"Source summary: {summary}\n"
        )
        self.deps.services.update_task(child_task.task_id, goal=updated_goal)
        self.deps.services.append_event(
            team_id=state["team_id"],
            run_id=state["run_id"],
            task_id=child_task.task_id,
            agent_id=worker.agent_id,
            event_type=event_type_name(EventType.TASK_HANDOFF_CREATED),
            payload=event_payload("Worker handoff created", source_task_id=parent_task.task_id, target_agent_id=worker.agent_id),
        )
        self.deps.services.assign_task(child_task.task_id, worker.agent_id)
        dispatch = self.deps.services.dispatch_task(worker.agent_id, self.deps.services.build_task_contract(child_task, worker))
        self.deps.services.append_event(
            team_id=state["team_id"],
            run_id=state["run_id"],
            task_id=child_task.task_id,
            agent_id=worker.agent_id,
            event_type=event_type_name(EventType.TASK_HANDOFF_DISPATCHED),
            payload=event_payload("Worker handoff dispatched", source_task_id=parent_task.task_id, prompt=dispatch.prompt),
        )
        return child_task

    def _create_redo_task(self, state: AlvisRunState, task, output, reason: str):
        source_task = self._redo_source_task(task)
        if self._redo_attempt_count(task) >= self.deps.services.settings.redo_attempt_limit:
            self.deps.services.append_event(
                team_id=state["team_id"],
                run_id=state["run_id"],
                task_id=task.task_id,
                agent_id=task.agent_id,
                event_type=event_type_name(EventType.ERROR_RAISED),
                payload=event_payload(
                    "Redo suppressed: source retry limit reached",
                    source_task_id=source_task.task_id,
                    reason=reason,
                ),
            )
            return None
        if any(
            candidate.title.startswith("Redo:")
            and self._redo_source_task(candidate).task_id == source_task.task_id
            and candidate.status in {TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value, TaskStatus.WAITING_INPUT.value}
            for candidate in self.deps.services.list_run_tasks(source_task.run_id)
        ):
            return None
        target_role_alias = source_task.target_role_alias
        owned_paths = source_task.owned_paths
        title = f"Redo: {source_task.title}"
        redo_task = self.deps.services.create_task(
            team_id=task.team_id,
            run_id=task.run_id,
            title=title,
            goal=(
                f"Redo the original task with a narrow, on-target response.\n"
                f"Original request: {state['user_request']}\n"
                f"Original goal: {source_task.goal}\n"
                f"Redo reason: {reason}\n"
                "Return only a valid ALVIS result block with real values.\n"
            ),
            review_required=False,
            target_role_alias=target_role_alias,
            owned_paths=owned_paths,
            task_type="worker",
            parent_task_id=task.task_id,
        )
        worker = next(
            (
                candidate
                for candidate in self.deps.services.list_worker_agents(state["team_id"])
                if (candidate.role_alias or candidate.role) == target_role_alias
            ),
            None,
        )
        if worker is None:
            return None
        self.deps.services.assign_task(redo_task.task_id, worker.agent_id)
        dispatch = self.deps.services.dispatch_task(worker.agent_id, self.deps.services.build_task_contract(redo_task, worker))
        self.deps.services.append_event(
            team_id=state["team_id"],
            run_id=state["run_id"],
            task_id=redo_task.task_id,
            agent_id=worker.agent_id,
            event_type=event_type_name(EventType.TASK_HANDOFF_CREATED),
            payload=event_payload("Redo task created", source_task_id=task.task_id, target_agent_id=worker.agent_id, reason=reason),
        )
        self.deps.services.append_event(
            team_id=state["team_id"],
            run_id=state["run_id"],
            task_id=redo_task.task_id,
            agent_id=worker.agent_id,
            event_type=event_type_name(EventType.TASK_HANDOFF_DISPATCHED),
            payload=event_payload("Redo task dispatched", source_task_id=task.task_id, prompt=dispatch.prompt),
        )
        return redo_task

    def ingest_request(self, state: AlvisRunState) -> AlvisRunState:
        team_id = state["team_id"]
        run = self.deps.services.create_run(team_id, state["user_request"])
        self.deps.services.finalize_run(run.run_id, RunStatus.RUNNING)
        self.deps.services.append_event(
            team_id=team_id,
            run_id=run.run_id,
            event_type=event_type_name(EventType.RUN_CREATED),
            payload=event_payload("Run created", request=state["user_request"]),
        )
        state["run_id"] = run.run_id
        state["status"] = RunStatus.RUNNING.value
        self._save_linear_checkpoint(state, "ingest_request")
        return state

    def plan_tasks(self, state: AlvisRunState) -> AlvisRunState:
        workers = [
            {"role": agent.role, "role_alias": agent.role_alias or agent.role}
            for agent in self.deps.services.list_worker_agents(state["team_id"])
        ]
        tasks = []
        created_tasks = []
        for task_spec in self.create_plan(state["user_request"], workers):
            parent_task_id = None
            parent_index = task_spec.get("parent_index")
            if parent_index is not None and 0 <= parent_index < len(created_tasks):
                parent_task_id = created_tasks[parent_index].task_id
            review_required = task_spec.get("review_required", False)
            task = self.deps.services.create_task(
                team_id=state["team_id"],
                run_id=state["run_id"],
                title=task_spec["title"],
                goal=task_spec["goal"],
                review_required=review_required,
                target_role_alias=task_spec.get("target_role_alias"),
                owned_paths=task_spec.get("owned_paths", []),
                parent_task_id=parent_task_id,
            )
            self.deps.services.append_event(
                team_id=state["team_id"],
                run_id=state["run_id"],
                task_id=task.task_id,
                event_type=event_type_name(EventType.TASK_CREATED),
                payload=event_payload("Task created", title=task.title, goal=task.goal),
            )
            created_tasks.append(task)
            tasks.append(
                {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "parent_task_id": task.parent_task_id,
                    "title": task.title,
                    "goal": task.goal,
                    "status": task.status,
                    "review_required": review_required,
                    "target_role_alias": task.target_role_alias,
                    "owned_paths": task.owned_paths,
                }
            )
        state["tasks"] = tasks
        self._save_linear_checkpoint(state, "plan_tasks")
        return state

    def select_agents(self, state: AlvisRunState) -> AlvisRunState:
        workers = self.deps.services.list_worker_agents(state["team_id"])
        if not workers:
            raise ValueError(f"team {state['team_id']} has no worker agents")
        assignments = []
        for task in state["tasks"]:
            worker = next(
                (
                    candidate
                    for candidate in workers
                    if (candidate.role_alias or candidate.role) == task.get("target_role_alias")
                ),
                None,
            )
            if worker is None:
                continue
            if task.get("parent_task_id"):
                continue
            assignments.append({"task_id": task["task_id"], "agent_id": worker.agent_id})
        state["assignments"] = assignments
        self._save_linear_checkpoint(state, "select_agents")
        return state

    def dispatch_tasks(self, state: AlvisRunState) -> AlvisRunState:
        for assignment in state["assignments"]:
            task = self.deps.services.get_task(assignment["task_id"])
            agent = self.deps.services.get_agent(assignment["agent_id"])
            contract = TaskContract(
                task_id=task.task_id,
                task_type=task.task_type,
                role=agent.role,
                role_alias=agent.role_alias,
                cwd=agent.cwd or str(self.deps.services.settings.repo_root),
                goal=task.goal,
                owned_paths=task.owned_paths,
                constraints=[
                    "Do not push changes.",
                    "Escalate review before commit.",
                    "Only work within the assigned files or paths.",
                    "Do not modify files outside owned_paths.",
                ],
                expected_output=[
                    "Summary",
                    "Changed files",
                    "Test results",
                    "Risks or blockers",
                ],
                coordination_context=[],
                context={"team_id": state["team_id"], "run_id": state["run_id"]},
            )
            dispatch_gate = self.deps.services.can_dispatch_task(task.task_id, agent.agent_id, require_live_session=False)
            if not dispatch_gate.ok:
                continue
            self.deps.services.assign_task(task.task_id, agent.agent_id)
            dispatch = self.deps.services.dispatch_task(agent.agent_id, contract)
            if not dispatch.ok:
                self.deps.services.update_task(task.task_id, status=TaskStatus.BLOCKED.value, result_summary=dispatch.reason)
                continue
            self.deps.services.append_event(
                team_id=state["team_id"],
                run_id=state["run_id"],
                agent_id=agent.agent_id,
                task_id=task.task_id,
                event_type=event_type_name(EventType.AGENT_PROMPT_SENT),
                payload=event_payload("Task dispatched", prompt=dispatch.prompt, task_title=task.title),
            )
        self._save_linear_checkpoint(state, "dispatch_tasks")
        return state

    def wait_for_updates(self, state: AlvisRunState) -> AlvisRunState:
        state = self._refresh_state_from_db(state)
        if state.get("pending_interactions"):
            self._save_linear_checkpoint(state, "wait_for_updates")
            return state
        outputs = self.deps.services.collect_outputs(state["team_id"])
        if not outputs:
            sleep(max(0.0, self.deps.services.settings.graph_poll_interval_seconds))
            self.deps.services.collect_outputs(state["team_id"])
        self._save_linear_checkpoint(state, "wait_for_updates")
        return state

    def evaluate_progress(self, state: AlvisRunState) -> AlvisRunState:
        state = self._refresh_state_from_db(state)
        state["leader_waiting"] = False
        state["waiting_for_leader_summary"] = None
        active = []
        completed = []
        blocked = []
        handoffs = []
        pending_interactions = []
        for task_state in state["tasks"]:
            task = self.deps.services.get_task(task_state["task_id"])
            output = self.deps.services.get_task_output(task.task_id)
            agent = self.deps.services.get_agent(task.agent_id) if task.agent_id else None

            if task.status == TaskStatus.DONE.value:
                completed.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.DONE.value,
                    }
                )
                if output and output.kind == "final" and task.parent_task_id and not state.get("final_output_candidate"):
                    state["final_output_candidate"] = {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "summary": output.summary,
                        "changed_files": output.changed_files,
                        "risk_flags": output.risk_flags,
                        "status_signal": output.status_signal,
                    }
                    state["final_output_ready"] = (output.status_signal or "done") == "done"
                continue

            if task.status == TaskStatus.BLOCKED.value:
                blocked.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.BLOCKED.value,
                    }
                )
                continue

            if task.parent_task_id and not task.agent_id:
                if task.status == TaskStatus.CREATED.value:
                    continue
                parent_task = self.deps.services.get_task(task.parent_task_id)
                if parent_task.status != TaskStatus.DONE.value:
                    continue

            if not output or output.kind != "final":
                active.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": task.status,
                    }
                )
                continue

            summary = output.summary if output else f"Task {task.title} dispatched and awaiting Codex output."
            changed_files = output.changed_files if output else []
            risk_flags = output.risk_flags if output else []
            status_signal = output.status_signal or "done"
            parse_failed = output.output_parse_status in {
                "no_result_block",
                "invalid_result_block",
                "schema_parse_failed",
                "schema_contract_failed",
            }
            runtime_failed = output.output_parse_status == "runtime_exec_failed"
            if output and task.parent_task_id:
                if parse_failed or runtime_failed:
                    self.deps.services.update_task(task.task_id, status=TaskStatus.BLOCKED.value, result_summary=summary)
                    blocked.append(
                        {
                            "task_id": task.task_id,
                            "agent_id": task.agent_id,
                            "title": task.title,
                            "goal": task.goal,
                            "status": TaskStatus.BLOCKED.value,
                        }
                    )
                    if parse_failed:
                        self.deps.services.append_event(
                            team_id=state["team_id"],
                            run_id=state["run_id"],
                            task_id=task.task_id,
                            agent_id=task.agent_id,
                            event_type=event_type_name(EventType.ERROR_RAISED),
                            payload=event_payload(
                                "응답 파싱 실패",
                                parse_status=output.output_parse_status,
                                task_summary=summary,
                            ),
                        )
                    continue
                if status_signal in {"blocked", "needs_review"} and not parse_failed:
                    redo_task = self._create_redo_task(state, task, output, summary)
                    self.deps.services.update_task(task.task_id, status=TaskStatus.DONE.value, result_summary=summary)
                    completed.append(
                        {
                            "task_id": task.task_id,
                            "agent_id": task.agent_id,
                            "title": task.title,
                            "goal": task.goal,
                            "status": TaskStatus.DONE.value,
                        }
                    )
                    state["final_output_candidate"] = {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "summary": summary,
                        "changed_files": changed_files,
                        "risk_flags": risk_flags,
                        "status_signal": status_signal,
                    }
                    state["final_output_ready"] = False
                    if redo_task:
                        handoffs.append(
                            {
                                "task_id": redo_task.task_id,
                                "agent_id": redo_task.agent_id,
                                "title": redo_task.title,
                                "goal": redo_task.goal,
                                "status": redo_task.status,
                            }
                        )
                    else:
                        blocked.append(
                            {
                                "task_id": task.task_id,
                                "agent_id": task.agent_id,
                                "title": task.title,
                                "goal": task.goal,
                                "status": TaskStatus.BLOCKED.value,
                            }
                        )
                    continue
                self.deps.services.update_task(task.task_id, status=TaskStatus.DONE.value, result_summary=summary)
                completed.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.DONE.value,
                    }
                )
                state["final_output_candidate"] = {
                    "task_id": task.task_id,
                    "agent_id": task.agent_id,
                    "summary": summary,
                    "changed_files": changed_files,
                    "risk_flags": risk_flags,
                    "status_signal": status_signal,
                }
                state["final_output_ready"] = status_signal == "done"
                self.deps.services.append_event(
                    team_id=state["team_id"],
                    run_id=state["run_id"],
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    event_type=event_type_name(EventType.LEADER_OUTPUT_READY),
                    payload=event_payload("Leader output ready", output_summary=summary),
                )
                continue
            if output and task.task_type == "leader":
                pending_for_run = [
                    item for item in self.deps.services.list_interactions(run_id=state["run_id"]) if item.status == "pending"
                ]
                source_task_ids = {item.task_id for item in pending_for_run if item.task_id}
                leader_guidance = "\n".join(output.followup_suggestion or [output.summary]).strip()
                for source_task_id in source_task_ids:
                    source_task = self.deps.services.get_task(source_task_id)
                    followup_goal = (
                        f"Continue the original task with leader guidance.\n"
                        f"Original goal: {source_task.goal}\n"
                        f"Leader guidance: {leader_guidance or output.summary}\n"
                    )
                    followup_task = self.deps.services.create_task(
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
                            for candidate in self.deps.services.list_worker_agents(state["team_id"])
                            if (candidate.role_alias or candidate.role) == source_task.target_role_alias
                        ),
                        None,
                    )
                    if worker:
                        self.deps.services.assign_task(followup_task.task_id, worker.agent_id)
                        dispatch = self.deps.services.dispatch_task(worker.agent_id, self.deps.services.build_task_contract(followup_task, worker))
                        self.deps.services.append_event(
                            team_id=state["team_id"],
                            run_id=state["run_id"],
                            task_id=followup_task.task_id,
                            agent_id=worker.agent_id,
                            event_type=event_type_name(EventType.LEADER_INSTRUCTION_CREATED),
                            payload=event_payload(
                                "Leader follow-up task created",
                                source_task_id=source_task.task_id,
                                new_task_id=followup_task.task_id,
                                prompt=dispatch.prompt,
                            ),
                        )
                    self.deps.services.update_task(
                        source_task.task_id,
                        status=TaskStatus.CANCELLED.value,
                        result_summary=f"Superseded by leader guidance: {leader_guidance or output.summary}",
                    )
                for interaction in pending_for_run:
                    self.deps.services.resolve_interaction(
                        interaction.interaction_id,
                        payload={"leader_summary": output.summary, "leader_guidance": output.followup_suggestion},
                    )
                self.deps.services.update_task(task.task_id, status=TaskStatus.DONE.value, result_summary=summary)
                completed.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.DONE.value,
                    }
                )
                continue
            if (
                output
                and task.task_type != "leader"
                and (
                    output.question_for_leader
                    or output.requested_context
                    or output.followup_suggestion
                    or output.dependency_note
                    or output.status_signal == "need_input"
                )
            ):
                existing = {
                    (item.kind, (item.payload or {}).get("message"))
                    for item in self.deps.services.list_interactions(run_id=task.run_id, status=InteractionStatus.PENDING)
                    if item.task_id == task.task_id
                }
                for spec in self.deps.services.interaction_specs_from_output(task, output):
                    key = (spec["kind"], spec.get("message"))
                    if key in existing:
                        continue
                    self.deps.services.create_interaction(
                        run_id=task.run_id,
                        team_id=task.team_id,
                        kind=spec["kind"],
                        payload=spec,
                        source_agent_id=task.agent_id,
                        target_role_alias=spec.get("target_role_alias"),
                        task_id=task.task_id,
                    )
                self.deps.services.update_task(task.task_id, status=TaskStatus.WAITING_INPUT.value, result_summary=summary)
                interaction_summaries = [
                    item.get("message")
                    for item in self.deps.services.summarize_pending_interactions(task.run_id)
                    if item.get("task_id") == task.task_id
                ]
                state["leader_waiting"] = True
                state["waiting_for_leader_summary"] = next((item for item in interaction_summaries if item), summary)
                pending_interactions.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.WAITING_INPUT.value,
                    }
                )
                continue
            if output and status_signal == "blocked":
                if parse_failed or runtime_failed:
                    self.deps.services.update_task(task.task_id, status=TaskStatus.BLOCKED.value, result_summary=summary)
                    blocked.append(
                        {
                            "task_id": task.task_id,
                            "agent_id": task.agent_id,
                            "title": task.title,
                            "goal": task.goal,
                            "status": TaskStatus.BLOCKED.value,
                        }
                    )
                    if parse_failed:
                        self.deps.services.append_event(
                            team_id=state["team_id"],
                            run_id=state["run_id"],
                            task_id=task.task_id,
                            agent_id=task.agent_id,
                            event_type=event_type_name(EventType.ERROR_RAISED),
                            payload=event_payload(
                                "응답 파싱 실패",
                                parse_status=output.output_parse_status,
                                task_summary=summary,
                            ),
                        )
                    continue
                redo_task = self._create_redo_task(state, task, output, summary)
                self.deps.services.update_task(task.task_id, status=TaskStatus.DONE.value, result_summary=summary)
                completed.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.DONE.value,
                    }
                )
                if redo_task:
                    handoffs.append(
                        {
                            "task_id": redo_task.task_id,
                            "agent_id": redo_task.agent_id,
                            "title": redo_task.title,
                            "goal": redo_task.goal,
                            "status": redo_task.status,
                        }
                    )
                else:
                    blocked.append(
                        {
                            "task_id": task.task_id,
                            "agent_id": task.agent_id,
                            "title": task.title,
                            "goal": task.goal,
                            "status": TaskStatus.BLOCKED.value,
                        }
                    )
                continue
            if output and agent and agent.role != AgentRole.REVIEWER.value:
                handoff_task = self._pending_child_task(task.task_id, title_prefix="Validate and summarize")
                if handoff_task is not None:
                    handoff_task = self._dispatch_child_task(state, task, handoff_task, summary)
                if handoff_task is None:
                    handoff_task = self._create_reviewer_handoff(state, task, output, summary)
                self.deps.services.update_task(task.task_id, status=TaskStatus.DONE.value, result_summary=summary)
                completed.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.DONE.value,
                    }
                )
                if handoff_task:
                    handoffs.append(
                        {
                            "task_id": handoff_task.task_id,
                            "agent_id": handoff_task.agent_id,
                            "title": handoff_task.title,
                            "goal": handoff_task.goal,
                            "status": handoff_task.status,
                        }
                    )
                continue
            if risk_flags:
                self.deps.services.update_task(task.task_id, status=TaskStatus.BLOCKED.value, result_summary=summary)
                blocked.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.BLOCKED.value,
                    }
                )
            else:
                self.deps.services.update_task(task.task_id, status=TaskStatus.DONE.value, result_summary=summary)
                completed.append(
                    {
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "title": task.title,
                        "goal": task.goal,
                        "status": TaskStatus.DONE.value,
                    }
                )
        state["completed_tasks"] = completed
        state["blocked_tasks"] = blocked
        state["handoffs"] = handoffs
        state["active_tasks"] = active + pending_interactions + handoffs
        state["pending_interactions"] = self.deps.services.summarize_pending_interactions(state["run_id"])
        state["review_requests"] = []
        state.setdefault("final_output_ready", False)
        if active or pending_interactions or handoffs:
            state["status"] = RunStatus.RUNNING.value
        elif blocked:
            state["status"] = RunStatus.FAILED.value
        else:
            state["status"] = RunStatus.DONE.value
        return state

    def route_interactions(self, state: AlvisRunState) -> AlvisRunState:
        state = self._refresh_state_from_db(state)
        pending = self.deps.services.summarize_pending_interactions(state["run_id"])
        state["pending_interactions"] = pending
        if pending:
            state["leader_waiting"] = True
            state["waiting_for_leader_summary"] = next((item.get("message") for item in pending if item.get("message")), None)
            for item in pending:
                self.deps.services.append_event(
                    team_id=state["team_id"],
                    run_id=state["run_id"],
                    task_id=item.get("task_id"),
                    agent_id=item.get("source_agent_id"),
                    event_type=event_type_name(EventType.INTERACTION_ROUTED),
                    payload=event_payload("Interaction routed to leader console", interaction_id=item.get("interaction_id")),
                )
            state["status"] = RunStatus.RUNNING.value
        return state

    def synthesize_result(self, state: AlvisRunState) -> AlvisRunState:
        state = self._refresh_state_from_db(state)
        if state.get("pending_interactions"):
            question = next((item.get("message") for item in state["pending_interactions"] if item.get("message")), None)
            final = f"Run is waiting for leader input. {question or 'Answer the pending worker question to continue.'}"
            status = RunStatus.RUNNING
        elif state["active_tasks"]:
            active_titles = ", ".join(task["title"] for task in state["active_tasks"])
            final = f"Run is still in progress. Waiting on tasks: {active_titles}."
            status = RunStatus.RUNNING
        elif state["blocked_tasks"]:
            blocked_titles = ", ".join(task["title"] for task in state["blocked_tasks"])
            final = f"Run is blocked. Tasks requiring attention: {blocked_titles}."
            status = RunStatus.FAILED
        elif state.get("final_output_candidate") and state.get("final_output_ready"):
            final = state["final_output_candidate"]["summary"]
            status = RunStatus.DONE
        elif state.get("final_output_candidate"):
            final = "Run requires another pass before a final answer can be shown."
            status = RunStatus.RUNNING
        else:
            task_titles = ", ".join(task["title"] for task in state["completed_tasks"]) or "No completed tasks"
            final = f"Run queued tasks successfully: {task_titles}."
            status = RunStatus.DONE
        self.deps.services.finalize_run(state["run_id"], status, final)
        if status != RunStatus.RUNNING:
            self.deps.services.clear_checkpoint(state["run_id"])
        state["final_response"] = final
        state["status"] = status.value
        return state
