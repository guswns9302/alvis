from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.enums import AgentRole, AgentStatus, ReviewStatus, RunStatus, TaskStatus


class TeamCreate(BaseModel):
    team_id: str
    worker_count: int = 2


class TeamSummary(BaseModel):
    team_id: str
    session_name: str | None = None
    created_at: datetime


class AgentSummary(BaseModel):
    agent_id: str
    team_id: str
    role: AgentRole
    status: AgentStatus
    cwd: str | None = None
    git_branch: str | None = None
    tmux_pane: str | None = None
    current_task_id: str | None = None


class TaskContract(BaseModel):
    task_id: str
    role: str
    cwd: str
    goal: str
    constraints: list[str] = Field(default_factory=list)
    expected_output: list[str] = Field(default_factory=list)
    completion_rule: str = "Reply with a concise completion summary and testing status."
    context: dict[str, Any] = Field(default_factory=dict)


class TaskSummary(BaseModel):
    task_id: str
    team_id: str
    run_id: str
    agent_id: str | None = None
    title: str
    goal: str
    status: TaskStatus
    review_required: bool = False


class ReviewSummary(BaseModel):
    review_id: str
    run_id: str
    task_id: str
    agent_id: str
    status: ReviewStatus
    summary: str
    created_at: datetime
    resolved_at: datetime | None = None


class RunSummary(BaseModel):
    run_id: str
    team_id: str
    request: str
    status: RunStatus
    created_at: datetime


class EventSummary(BaseModel):
    event_id: int
    run_id: str | None
    team_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
