from enum import StrEnum


class AgentRole(StrEnum):
    LEADER = "leader"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    ANALYST = "analyst"


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
    WAITING_INPUT = "waiting_input"
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


class InteractionStatus(StrEnum):
    PENDING = "pending"
    ROUTED = "routed"
    RESOLVED = "resolved"


class EventType(StrEnum):
    TEAM_CREATED = "team.created"
    RUN_CREATED = "run.created"
    RUN_RESUMED = "run.resumed"
    WORKTREE_CONFLICT_DETECTED = "worktree.conflict.detected"
    REPLAN_REQUESTED = "replan.requested"
    REPLAN_GENERATED = "replan.generated"
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_HANDOFF_CREATED = "task.handoff.created"
    TASK_HANDOFF_DISPATCHED = "task.handoff.dispatched"
    TASK_HANDOFF_COMPLETED = "task.handoff.completed"
    TASK_RETRY_REQUESTED = "task.retry.requested"
    TASK_RETRY_SUCCEEDED = "task.retry.succeeded"
    TASK_RETRY_SKIPPED = "task.retry.skipped"
    AGENT_PROMPT_SENT = "agent.prompt.sent"
    AGENT_OUTPUT_DELTA = "agent.output.delta"
    AGENT_OUTPUT_FINAL = "agent.output.final"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_STATUS_CHANGED = "agent.status.changed"
    REVIEW_REQUESTED = "review.requested"
    REVIEW_APPROVED = "review.approved"
    REVIEW_REJECTED = "review.rejected"
    INTERACTION_CREATED = "interaction.created"
    INTERACTION_ROUTED = "interaction.routed"
    INTERACTION_RESOLVED = "interaction.resolved"
    LEADER_PROMPT_SENT = "leader.prompt.sent"
    LEADER_INSTRUCTION_CREATED = "leader.instruction.created"
    LEADER_OUTPUT_READY = "leader.output.ready"
    SESSION_STARTED = "session.started"
    SESSION_EXITED = "session.exited"
    ERROR_RAISED = "error.raised"
