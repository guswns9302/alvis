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
from app.enums import EventType, RunStatus, TaskStatus
from app.graph.state import AlvisRunState
from app.logging import get_logger
from app.reviews.gate import ReviewGate
from app.schemas import TaskContract


@dataclass
class SupervisorDeps:
    services: Any


class Supervisor:
    def __init__(self, deps: SupervisorDeps):
        self.deps = deps
        self.log = get_logger(__name__)

    def create_plan(self, request: str) -> list[dict[str, Any]]:
        return [
            {
                "title": "Analyze request",
                "goal": f"Understand the request and identify key change areas: {request}",
                "worker_index": 0,
            },
            {
                "title": "Implement changes",
                "goal": f"Implement the requested work for: {request}",
                "worker_index": 1,
            },
            {
                "title": "Validate and summarize",
                "goal": f"Review outputs, identify risks, and summarize status for: {request}",
                "worker_index": 0,
            },
        ]

    def build_graph(self):
        if StateGraph is None:
            return None
        graph = StateGraph(AlvisRunState)
        graph.add_node("ingest_request", self.ingest_request)
        graph.add_node("plan_tasks", self.plan_tasks)
        graph.add_node("select_agents", self.select_agents)
        graph.add_node("dispatch_tasks", self.dispatch_tasks)
        graph.add_node("wait_for_updates", self.wait_for_updates)
        graph.add_node("evaluate_progress", self.evaluate_progress)
        graph.add_node("synthesize_result", self.synthesize_result)
        graph.add_edge("ingest_request", "plan_tasks")
        graph.add_edge("plan_tasks", "select_agents")
        graph.add_edge("select_agents", "dispatch_tasks")
        graph.add_edge("dispatch_tasks", "wait_for_updates")
        graph.add_edge("wait_for_updates", "evaluate_progress")
        graph.add_edge("evaluate_progress", "synthesize_result")
        graph.add_edge("synthesize_result", END)
        graph.set_entry_point("ingest_request")
        return graph.compile()

    def run(self, team_id: str, request: str) -> dict[str, Any]:
        state = AlvisRunState(
            team_id=team_id,
            user_request=request,
            tasks=[],
            assignments=[],
            completed_tasks=[],
            blocked_tasks=[],
            review_requests=[],
            status=RunStatus.CREATED.value,
        )
        graph = self.build_graph()
        if graph is None:
            return self._run_without_langgraph(state)
        return graph.invoke(state)

    def _run_without_langgraph(self, state: AlvisRunState) -> AlvisRunState:
        state = self.ingest_request(state)
        state = self.plan_tasks(state)
        state = self.select_agents(state)
        state = self.dispatch_tasks(state)
        state = self.wait_for_updates(state)
        state = self.evaluate_progress(state)
        state = self.synthesize_result(state)
        return state

    def ingest_request(self, state: AlvisRunState) -> AlvisRunState:
        team_id = state["team_id"]
        run = self.deps.services.create_run(team_id, state["user_request"])
        self.deps.services.append_event(
            team_id=team_id,
            run_id=run.run_id,
            event_type=event_type_name(EventType.RUN_CREATED),
            payload=event_payload("Run created", request=state["user_request"]),
        )
        state["run_id"] = run.run_id
        state["status"] = RunStatus.RUNNING.value
        return state

    def plan_tasks(self, state: AlvisRunState) -> AlvisRunState:
        tasks = []
        for idx, task_spec in enumerate(self.create_plan(state["user_request"]), start=1):
            review_required = idx == 3
            task = self.deps.services.create_task(
                team_id=state["team_id"],
                run_id=state["run_id"],
                title=task_spec["title"],
                goal=task_spec["goal"],
                review_required=review_required,
            )
            self.deps.services.append_event(
                team_id=state["team_id"],
                run_id=state["run_id"],
                task_id=task.task_id,
                event_type=event_type_name(EventType.TASK_CREATED),
                payload=event_payload("Task created", title=task.title, goal=task.goal),
            )
            tasks.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "goal": task.goal,
                    "status": task.status,
                    "review_required": review_required,
                    "worker_index": task_spec["worker_index"],
                }
            )
        state["tasks"] = tasks
        return state

    def select_agents(self, state: AlvisRunState) -> AlvisRunState:
        workers = self.deps.services.list_worker_agents(state["team_id"])
        if not workers:
            raise ValueError(f"team {state['team_id']} has no worker agents")
        assignments = []
        for task in state["tasks"]:
            worker = workers[task["worker_index"] % len(workers)]
            assignments.append({"task_id": task["task_id"], "agent_id": worker.agent_id})
        state["assignments"] = assignments
        return state

    def dispatch_tasks(self, state: AlvisRunState) -> AlvisRunState:
        for assignment in state["assignments"]:
            task = self.deps.services.get_task(assignment["task_id"])
            agent = self.deps.services.get_agent(assignment["agent_id"])
            self.deps.services.assign_task(task.task_id, agent.agent_id)
            contract = TaskContract(
                task_id=task.task_id,
                role=agent.role,
                cwd=agent.cwd or str(self.deps.services.settings.repo_root),
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
                context={"team_id": state["team_id"], "run_id": state["run_id"]},
            )
            prompt = self.deps.services.dispatch_task(agent.agent_id, contract)
            self.deps.services.append_event(
                team_id=state["team_id"],
                run_id=state["run_id"],
                agent_id=agent.agent_id,
                task_id=task.task_id,
                event_type=event_type_name(EventType.AGENT_PROMPT_SENT),
                payload=event_payload("Task dispatched", prompt=prompt, task_title=task.title),
            )
        return state

    def wait_for_updates(self, state: AlvisRunState) -> AlvisRunState:
        sleep(1)
        self.deps.services.collect_outputs(state["team_id"])
        return state

    def evaluate_progress(self, state: AlvisRunState) -> AlvisRunState:
        gate = ReviewGate()
        completed = []
        blocked = []
        reviews = []
        for task_state in state["tasks"]:
            task = self.deps.services.get_task(task_state["task_id"])
            output = self.deps.services.get_task_output(task.task_id)
            summary = output.summary if output else f"Task {task.title} dispatched and awaiting Codex output."
            changed_files = output.changed_files if output else []
            risk_flags = output.risk_flags if output else []
            review = gate.evaluate(summary, changed_files=changed_files)
            if review.needs_review or task.review_required:
                review_request = self.deps.services.create_review(
                    run_id=state["run_id"],
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    summary=f"Review required for task {task.title}",
                    details={
                        "reason": review.reason or "task marked review_required",
                        "summary": summary,
                        "changed_files": changed_files,
                        "risk_flags": risk_flags,
                    },
                )
                self.deps.services.append_event(
                    team_id=state["team_id"],
                    run_id=state["run_id"],
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    event_type=event_type_name(EventType.REVIEW_REQUESTED),
                    payload=event_payload("Review requested", review_id=review_request.review_id, reason=review.reason),
                )
                self.deps.services.update_task(task.task_id, status=TaskStatus.WAITING_REVIEW.value, result_summary=summary)
                reviews.append(
                    {
                        "review_id": review_request.review_id,
                        "task_id": task.task_id,
                        "agent_id": task.agent_id,
                        "status": review_request.status,
                        "summary": review_request.summary,
                    }
                )
            elif risk_flags:
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
        state["review_requests"] = reviews
        if reviews:
            state["status"] = RunStatus.WAITING_REVIEW.value
        elif blocked:
            state["status"] = RunStatus.FAILED.value
        else:
            state["status"] = RunStatus.DONE.value
        return state

    def synthesize_result(self, state: AlvisRunState) -> AlvisRunState:
        if state["review_requests"]:
            final = "Run is waiting for review approvals before final synthesis."
            status = RunStatus.WAITING_REVIEW
        elif state["blocked_tasks"]:
            blocked_titles = ", ".join(task["title"] for task in state["blocked_tasks"])
            final = f"Run is blocked. Tasks requiring attention: {blocked_titles}."
            status = RunStatus.FAILED
        else:
            task_titles = ", ".join(task["title"] for task in state["completed_tasks"]) or "No completed tasks"
            final = f"Run queued tasks successfully: {task_titles}."
            status = RunStatus.DONE
        self.deps.services.finalize_run(state["run_id"], status, final)
        state["final_response"] = final
        state["status"] = status.value
        return state
