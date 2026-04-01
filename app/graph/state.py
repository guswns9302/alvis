from __future__ import annotations

from typing import TypedDict


class TaskState(TypedDict, total=False):
    task_id: str
    agent_id: str
    task_type: str
    parent_task_id: str | None
    title: str
    goal: str
    status: str
    review_required: bool
    target_role_alias: str | None
    owned_paths: list[str]


class ReviewState(TypedDict, total=False):
    review_id: str
    task_id: str
    agent_id: str
    status: str
    summary: str


class InteractionState(TypedDict, total=False):
    interaction_id: str
    task_id: str | None
    source_agent_id: str | None
    target_agent_id: str | None
    target_role_alias: str | None
    kind: str
    status: str
    payload: dict


class AlvisRunState(TypedDict, total=False):
    run_id: str
    team_id: str
    user_request: str
    tasks: list[TaskState]
    assignments: list[dict]
    active_tasks: list[TaskState]
    completed_tasks: list[TaskState]
    blocked_tasks: list[TaskState]
    review_requests: list[ReviewState]
    handoffs: list[dict]
    pending_interactions: list[InteractionState]
    leader_waiting: bool
    waiting_for_leader_summary: str | None
    final_output_candidate: dict | None
    final_output_ready: bool
    final_response: str | None
    status: str
