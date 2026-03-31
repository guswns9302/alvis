from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.enums import AgentRole, AgentStatus, ReviewStatus, RunStatus, TaskStatus


class TeamModel(Base):
    __tablename__ = "teams"

    team_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    agents: Mapped[list["AgentModel"]] = relationship(back_populates="team")


class AgentModel(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id"), index=True)
    role: Mapped[str] = mapped_column(String, default=AgentRole.IMPLEMENTER.value)
    status: Mapped[str] = mapped_column(String, default=AgentStatus.IDLE.value)
    cwd: Mapped[str | None] = mapped_column(String, nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String, nullable=True)
    git_worktree_path: Mapped[str | None] = mapped_column(String, nullable=True)
    tmux_session: Mapped[str | None] = mapped_column(String, nullable=True)
    tmux_window: Mapped[str | None] = mapped_column(String, nullable=True)
    tmux_pane: Mapped[str | None] = mapped_column(String, nullable=True)
    current_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped["TeamModel"] = relationship(back_populates="agents")


class RunModel(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id"), index=True)
    request: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default=RunStatus.CREATED.value)
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RunCheckpointModel(Base):
    __tablename__ = "run_checkpoints"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    next_node: Mapped[str] = mapped_column(String)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TaskModel(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    title: Mapped[str] = mapped_column(String)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default=TaskStatus.CREATED.value)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.agent_id"), nullable=True)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TaskAssignmentModel(Base):
    __tablename__ = "task_assignments"

    assignment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReviewRequestModel(Base):
    __tablename__ = "reviews"

    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id"))
    status: Mapped[str] = mapped_column(String, default=ReviewStatus.PENDING.value)
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SessionModel(Base):
    __tablename__ = "sessions"

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id"), index=True)
    tmux_session: Mapped[str] = mapped_column(String)
    tmux_window: Mapped[str | None] = mapped_column(String, nullable=True)
    tmux_pane: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="started")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EventModel(Base):
    __tablename__ = "events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    team_id: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
