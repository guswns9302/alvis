from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.enums import AgentRole, AgentStatus, ReviewStatus, RunStatus, TaskStatus


class TeamCreate(BaseModel):
    team_id: str
    worker_1_role: str
    worker_2_role: str


class TeamSummary(BaseModel):
    team_id: str
    session_name: str | None = None
    created_at: datetime


class AgentSummary(BaseModel):
    agent_id: str
    team_id: str
    role: AgentRole
    role_alias: str | None = None
    status: AgentStatus
    cwd: str | None = None
    tmux_pane: str | None = None
    current_task_id: str | None = None


class TaskContract(BaseModel):
    task_id: str
    task_type: str = "worker"
    role: str
    role_alias: str | None = None
    cwd: str
    goal: str
    owned_paths: list[str] = Field(default_factory=list)
    coordination_context: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    expected_output: list[str] = Field(default_factory=list)
    completion_rule: str = (
        "Finish by printing a structured result block using this exact format:\n"
        "ALVIS_RESULT_START\n"
        "STATUS: <done|need_input|blocked|needs_review>\n"
        "SUMMARY: <one concise summary line>\n"
        "QUESTION_FOR_LEADER:\n"
        "- <question that requires leader guidance>\n"
        "REQUESTED_CONTEXT:\n"
        "- <missing context or dependency>\n"
        "FOLLOWUP_SUGGESTION:\n"
        "- <suggested next instruction>\n"
        "DEPENDENCY_NOTE:\n"
        "- <cross-agent dependency note>\n"
        "CHANGED_FILES:\n"
        "- <path or file summary>\n"
        "TEST_RESULTS:\n"
        "- <test result>\n"
        "RISK_FLAGS:\n"
        "- <risk or blocker>\n"
        "ALVIS_RESULT_END\n"
        "If a section has no items, leave it empty but keep the section header."
    )
    context: dict[str, Any] = Field(default_factory=dict)


class TaskSummary(BaseModel):
    task_id: str
    team_id: str
    run_id: str
    agent_id: str | None = None
    task_type: str = "worker"
    parent_task_id: str | None = None
    title: str
    goal: str
    target_role_alias: str | None = None
    owned_paths: list[str] = Field(default_factory=list)
    status: TaskStatus
    review_required: bool = False


class AgentOutput(BaseModel):
    task_id: str | None = None
    agent_id: str
    kind: str
    summary: str
    status_signal: str | None = None
    question_for_leader: list[str] = Field(default_factory=list)
    requested_context: list[str] = Field(default_factory=list)
    followup_suggestion: list[str] = Field(default_factory=list)
    dependency_note: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    test_results: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class DispatchResult(BaseModel):
    ok: bool
    reason: str | None = None
    prompt: str | None = None


class ReviewSummary(BaseModel):
    review_id: str
    run_id: str
    task_id: str
    agent_id: str
    status: ReviewStatus
    summary: str
    created_at: datetime
    resolved_at: datetime | None = None


class ReplanResult(BaseModel):
    review_id: str
    parent_task_id: str
    new_task_id: str
    assigned_agent_id: str
    reason: str


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
