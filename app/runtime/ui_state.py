from __future__ import annotations

from collections.abc import Iterable

from app.enums import AgentRole, AgentStatus, EventType


WORKER_LOG_EVENTS = {
    EventType.TASK_CREATED.value,
    EventType.TASK_ASSIGNED.value,
    EventType.TASK_HANDOFF_CREATED.value,
    EventType.TASK_HANDOFF_DISPATCHED.value,
    EventType.TASK_HANDOFF_COMPLETED.value,
    EventType.AGENT_OUTPUT_FINAL.value,
    EventType.INTERACTION_CREATED.value,
    EventType.INTERACTION_ROUTED.value,
    EventType.INTERACTION_RESOLVED.value,
    EventType.LEADER_OUTPUT_READY.value,
    EventType.ERROR_RAISED.value,
}

LEADER_LOG_EVENTS = WORKER_LOG_EVENTS | {
    EventType.RUN_CREATED.value,
    EventType.RUN_RESUMED.value,
    EventType.LEADER_INSTRUCTION_CREATED.value,
}


def worker_agents(status: dict) -> list[dict]:
    return [agent for agent in status.get("agents", []) if agent.get("role") != AgentRole.LEADER.value]


def task_by_id(status: dict, task_id: str | None) -> dict | None:
    if not task_id:
        return None
    return next((task for task in status.get("tasks", []) if task["task_id"] == task_id), None)


def status_signal(agent: dict) -> str:
    raw = agent.get("status", AgentStatus.IDLE.value)
    if raw in {AgentStatus.RUNNING.value, AgentStatus.ASSIGNED.value, AgentStatus.WAITING_INPUT.value}:
        return "active"
    if raw == AgentStatus.IDLE.value:
        return "idle"
    if raw == AgentStatus.DONE.value:
        return "done"
    if raw in {AgentStatus.BLOCKED.value, AgentStatus.FAILED.value}:
        return "error"
    return "unknown"


def signal_dot(kind: str, blink_on: bool) -> str:
    if kind == "active":
        color = 92 if blink_on else 32
        glyph = "●" if blink_on else "◉"
    elif kind == "idle":
        color = 33
        glyph = "●"
    elif kind == "done":
        color = 36
        glyph = "●"
    elif kind == "error":
        color = 31
        glyph = "●"
    else:
        color = 90
        glyph = "●"
    return f"\x1b[{color}m{glyph}\x1b[0m"


def compact_task_title(status: dict, agent: dict, width: int = 44) -> str:
    task = task_by_id(status, agent.get("task"))
    if not task:
        return "대기 중"
    title = task.get("title") or "작업 없음"
    if title.startswith("Redo:"):
        attempts = task.get("redo_attempt_count") or 0
        limit = 1 if task.get("redo_limit_reached") else max(attempts, 1)
        title = f"{title} ({attempts}/{limit})"
    if len(title) <= width:
        return title
    return title[: width - 3].rstrip() + "..."


def summarize_event(event) -> str:
    payload = event.payload or {}
    summary = payload.get("summary") or event.event_type
    if event.event_type == EventType.AGENT_OUTPUT_FINAL.value:
        output_summary = payload.get("summary") or payload.get("output_summary")
        if output_summary:
            summary = output_summary
    elif event.event_type == EventType.TASK_HANDOFF_CREATED.value and str(summary).startswith("Redo task"):
        summary = "재작업 생성"
    elif event.event_type == EventType.TASK_HANDOFF_DISPATCHED.value and str(summary).startswith("Redo task"):
        summary = "재작업 전달"
    return str(summary).strip()


def format_timeline_entry(event, status: dict) -> str:
    agent_id = event.agent_id or "system"
    alias = agent_id
    if event.agent_id:
        match = next((agent for agent in status.get("agents", []) if agent["agent_id"] == event.agent_id), None)
        if match:
            alias = f"{event.agent_id.split('-')[-1]}/{match.get('role_alias') or match.get('role')}"
    return f"[{alias}] {summarize_event(event)}"


def filtered_events(services, team_id: str, visible: Iterable[str]) -> list:
    visible_set = set(visible)
    return [event for event in services.list_events(team_id=team_id) if event.event_type in visible_set]
