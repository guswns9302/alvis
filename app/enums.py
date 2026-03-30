from enum import StrEnum


class AgentRole(StrEnum):
    LEADER = "leader"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"


class AgentStatus(StrEnum):
    IDLE = "idle"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_REVIEW = "waiting_review"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


class TaskStatus(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    DONE = "done"
    FAILED = "failed"


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    AGENT_PROMPT_SENT = "agent.prompt.sent"
    AGENT_OUTPUT_DELTA = "agent.output.delta"
    AGENT_OUTPUT_FINAL = "agent.output.final"
    AGENT_STATUS_CHANGED = "agent.status.changed"
    REVIEW_REQUESTED = "review.requested"
    REVIEW_APPROVED = "review.approved"
    REVIEW_REJECTED = "review.rejected"
    SESSION_STARTED = "session.started"
    SESSION_EXITED = "session.exited"
    ERROR_RAISED = "error.raised"
