from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentModel,
    EventModel,
    ReviewRequestModel,
    RunModel,
    RunCheckpointModel,
    SessionModel,
    TaskAssignmentModel,
    TaskModel,
    TeamModel,
)
from app.enums import AgentRole, AgentStatus, ReviewStatus, RunStatus, TaskStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def create_team(self, team_id: str, worker_count: int, session_name: str) -> TeamModel:
        team = TeamModel(team_id=team_id, session_name=session_name)
        self.session.add(team)
        leader = AgentModel(
            agent_id=f"{team_id}-leader",
            team_id=team_id,
            role=AgentRole.LEADER.value,
            status=AgentStatus.IDLE.value,
        )
        self.session.add(leader)
        for idx in range(worker_count):
            self.session.add(
                AgentModel(
                    agent_id=f"{team_id}-worker-{idx + 1}",
                    team_id=team_id,
                    role=AgentRole.IMPLEMENTER.value,
                    status=AgentStatus.IDLE.value,
                )
            )
        self.session.flush()
        return team

    def get_team(self, team_id: str) -> TeamModel | None:
        return self.session.get(TeamModel, team_id)

    def list_agents(self, team_id: str) -> list[AgentModel]:
        stmt = select(AgentModel).where(AgentModel.team_id == team_id).order_by(AgentModel.agent_id)
        return list(self.session.scalars(stmt))

    def list_all_agents(self) -> list[AgentModel]:
        stmt = select(AgentModel).order_by(AgentModel.agent_id)
        return list(self.session.scalars(stmt))

    def get_agent(self, agent_id: str) -> AgentModel | None:
        return self.session.get(AgentModel, agent_id)

    def update_agent(self, agent: AgentModel, **values) -> AgentModel:
        for key, value in values.items():
            setattr(agent, key, value)
        self.session.add(agent)
        self.session.flush()
        return agent

    def create_run(self, team_id: str, request: str) -> RunModel:
        run = RunModel(
            run_id=f"run-{uuid4().hex[:12]}",
            team_id=team_id,
            request=request,
            status=RunStatus.CREATED.value,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.session.add(run)
        self.session.flush()
        return run

    def get_run(self, run_id: str) -> RunModel | None:
        return self.session.get(RunModel, run_id)

    def get_checkpoint(self, run_id: str) -> RunCheckpointModel | None:
        return self.session.get(RunCheckpointModel, run_id)

    def list_team_runs(self, team_id: str) -> list[RunModel]:
        stmt = select(RunModel).where(RunModel.team_id == team_id).order_by(RunModel.created_at.desc())
        return list(self.session.scalars(stmt))

    def list_all_runs(self) -> list[RunModel]:
        stmt = select(RunModel).order_by(RunModel.created_at.desc())
        return list(self.session.scalars(stmt))

    def create_task(
        self,
        team_id: str,
        run_id: str,
        title: str,
        goal: str,
        review_required: bool = False,
    ) -> TaskModel:
        task = TaskModel(
            task_id=f"task-{uuid4().hex[:12]}",
            team_id=team_id,
            run_id=run_id,
            title=title,
            goal=goal,
            review_required=review_required,
            status=TaskStatus.CREATED.value,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.session.add(task)
        self.session.flush()
        return task

    def list_run_tasks(self, run_id: str) -> list[TaskModel]:
        stmt = select(TaskModel).where(TaskModel.run_id == run_id).order_by(TaskModel.created_at.asc())
        return list(self.session.scalars(stmt))

    def list_all_tasks(self) -> list[TaskModel]:
        stmt = select(TaskModel).order_by(TaskModel.created_at.asc())
        return list(self.session.scalars(stmt))

    def assign_task(self, task: TaskModel, agent: AgentModel) -> TaskAssignmentModel:
        task.agent_id = agent.agent_id
        task.status = TaskStatus.ASSIGNED.value
        task.updated_at = utcnow()
        agent.current_task_id = task.task_id
        agent.status = AgentStatus.ASSIGNED.value
        assignment = TaskAssignmentModel(task_id=task.task_id, agent_id=agent.agent_id, assigned_at=utcnow())
        self.session.add_all([task, agent, assignment])
        self.session.flush()
        return assignment

    def update_task(self, task: TaskModel, **values) -> TaskModel:
        for key, value in values.items():
            setattr(task, key, value)
        task.updated_at = utcnow()
        self.session.add(task)
        self.session.flush()
        return task

    def create_review(self, run_id: str, task_id: str, agent_id: str, summary: str, details: dict) -> ReviewRequestModel:
        review = ReviewRequestModel(
            review_id=f"review-{uuid4().hex[:12]}",
            run_id=run_id,
            task_id=task_id,
            agent_id=agent_id,
            summary=summary,
            details=details,
            status=ReviewStatus.PENDING.value,
            created_at=utcnow(),
        )
        self.session.add(review)
        self.session.flush()
        return review

    def list_reviews(self, status: ReviewStatus | None = None) -> list[ReviewRequestModel]:
        stmt = select(ReviewRequestModel).order_by(ReviewRequestModel.created_at.desc())
        if status:
            stmt = stmt.where(ReviewRequestModel.status == status.value)
        return list(self.session.scalars(stmt))

    def get_review(self, review_id: str) -> ReviewRequestModel | None:
        return self.session.get(ReviewRequestModel, review_id)

    def resolve_review(self, review: ReviewRequestModel, approved: bool) -> ReviewRequestModel:
        review.status = ReviewStatus.APPROVED.value if approved else ReviewStatus.REJECTED.value
        review.resolved_at = utcnow()
        self.session.add(review)
        self.session.flush()
        return review

    def add_session(self, team_id: str, agent_id: str, tmux_session: str, tmux_window: str | None, tmux_pane: str | None) -> SessionModel:
        session = SessionModel(
            team_id=team_id,
            agent_id=agent_id,
            tmux_session=tmux_session,
            tmux_window=tmux_window,
            tmux_pane=tmux_pane,
        )
        self.session.add(session)
        self.session.flush()
        return session

    def append_event(
        self,
        *,
        team_id: str,
        event_type: str,
        payload: dict,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> EventModel:
        event = EventModel(
            team_id=team_id,
            run_id=run_id,
            agent_id=agent_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
            created_at=utcnow(),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_events(self, team_id: str | None = None, run_id: str | None = None) -> list[EventModel]:
        stmt = select(EventModel).order_by(EventModel.created_at.asc(), EventModel.event_id.asc())
        if team_id:
            stmt = stmt.where(EventModel.team_id == team_id)
        if run_id:
            stmt = stmt.where(EventModel.run_id == run_id)
        return list(self.session.scalars(stmt))

    def latest_event(
        self,
        *,
        event_type: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> EventModel | None:
        stmt = select(EventModel)
        if event_type:
            stmt = stmt.where(EventModel.event_type == event_type)
        if agent_id:
            stmt = stmt.where(EventModel.agent_id == agent_id)
        if task_id:
            stmt = stmt.where(EventModel.task_id == task_id)
        stmt = stmt.order_by(EventModel.created_at.desc(), EventModel.event_id.desc())
        return self.session.scalars(stmt).first()

    def mark_run_status(self, run: RunModel, status: RunStatus, final_response: str | None = None) -> RunModel:
        run.status = status.value
        run.updated_at = utcnow()
        if final_response is not None:
            run.final_response = final_response
        self.session.add(run)
        self.session.flush()
        return run

    def save_checkpoint(self, run_id: str, thread_id: str, next_node: str, state: dict) -> RunCheckpointModel:
        checkpoint = self.get_checkpoint(run_id)
        if checkpoint is None:
            checkpoint = RunCheckpointModel(
                run_id=run_id,
                thread_id=thread_id,
                next_node=next_node,
                state=state,
                updated_at=utcnow(),
            )
        else:
            checkpoint.thread_id = thread_id
            checkpoint.next_node = next_node
            checkpoint.state = state
            checkpoint.updated_at = utcnow()
        self.session.add(checkpoint)
        self.session.flush()
        return checkpoint

    def delete_checkpoint(self, run_id: str) -> None:
        checkpoint = self.get_checkpoint(run_id)
        if checkpoint is not None:
            self.session.delete(checkpoint)
            self.session.flush()

    def find_stalled_agents(self, threshold_seconds: int) -> list[AgentModel]:
        cutoff = utcnow().timestamp() - threshold_seconds
        agents = self.session.scalars(select(AgentModel)).all()
        return [
            agent
            for agent in agents
            if agent.last_heartbeat_at and agent.last_heartbeat_at.timestamp() < cutoff
        ]
