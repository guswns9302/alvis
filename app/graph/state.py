from __future__ import annotations

from typing import TypedDict


class TaskState(TypedDict, total=False):
    task_id: str
    agent_id: str
    title: str
    goal: str
    status: str
    review_required: bool


class ReviewState(TypedDict, total=False):
    review_id: str
    task_id: str
    agent_id: str
    status: str
    summary: str


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
    final_response: str | None
    status: str
