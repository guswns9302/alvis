from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.text import Text
from rich.table import Table

IMPORTANT_EVENT_TYPES = {
    "run.created",
    "run.resumed",
    "task.assigned",
    "task.handoff.created",
    "task.handoff.completed",
    "agent.output.delta",
    "agent.output.final",
    "review.requested",
    "review.approved",
    "review.rejected",
    "interaction.created",
    "interaction.resolved",
    "leader.output.ready",
    "error.raised",
}

PARSE_STATUS_MESSAGES = {
    "no_result_block": "구조화된 응답을 만들지 못했습니다.",
    "invalid_result_block": "구조화된 응답 형식이 올바르지 않습니다.",
    "schema_parse_failed": "구조화된 응답을 JSON으로 해석하지 못했습니다.",
    "schema_contract_failed": "구조화된 응답이 기대 계약과 맞지 않습니다.",
}

STATUS_STYLES = {
    "assigned": "green",
    "running": "green",
    "waiting_input": "cyan",
    "waiting_review": "cyan",
    "idle": "yellow",
    "blocked": "red",
    "failed": "red",
    "done": "blue",
}

MESSAGE_TONES = {
    "system": ("bold #7f8c8d", "dim #aeb6bf"),
    "worker": ("bold #95a5a6", "dim #c0c6cc"),
    "warning": ("bold yellow", "yellow"),
    "error": ("bold red", "red"),
    "user": ("bold bright_cyan", "bright_cyan"),
    "output": ("bold bright_cyan", "bright_white"),
    "status": ("bold cyan", "cyan"),
}


def _friendly_background_error(exc: Exception) -> str:
    message = str(exc)
    if "GRAPH_RECURSION_LIMIT" in message or "Recursion limit" in message:
        return "요청이 아직 실행 중입니다. 워커 결과를 수집하는 중이며 상태를 다시 확인해 주세요."
    return message


def _status_style(status: str | None) -> str:
    return STATUS_STYLES.get((status or "").lower(), "white")


def _truncate(value: str | None, *, length: int = 44) -> str:
    text = (value or "-").strip() or "-"
    if len(text) <= length:
        return text
    return text[: max(0, length - 1)].rstrip() + "…"


def _tasks_by_id(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(task.get("task_id")): task
        for task in status.get("tasks", [])
        if task.get("task_id")
    }


def _worker_task_summary(agent: dict[str, Any], status: dict[str, Any]) -> str:
    task_id = agent.get("task")
    if not task_id:
        return "-"
    task = _tasks_by_id(status).get(str(task_id))
    if not task:
        return str(task_id)
    latest_output = task.get("latest_output") or {}
    return _truncate(
        task.get("title")
        or latest_output.get("summary")
        or task.get("goal")
        or str(task_id),
        length=48,
    )


def render_worker_strip(status: dict[str, Any]) -> RenderableType:
    workers = [agent for agent in status.get("agents", []) if agent.get("role") != "leader"]
    if not workers:
        return Text("workers: -", style="dim")
    segments: list[Text] = []
    for agent in workers:
        worker_name = str(agent.get("role_alias") or agent.get("role") or "-")
        worker_status = str(agent.get("status") or "-")
        summary = _worker_task_summary(agent, status)
        if segments:
            segments.append(Text("  |  ", style="dim"))
        segments.append(Text(worker_name, style="bold"))
        segments.append(Text(" "))
        segments.append(Text(worker_status, style=_status_style(worker_status)))
        segments.append(Text(" "))
        segments.append(Text(summary, style="white"))
    return Group(Text("Workers", style="bold green"), Group(*segments))


def render_session_header(team_id: str, status: dict[str, Any]) -> RenderableType:
    latest_run = status.get("latest_run") or {}
    run_id = latest_run.get("run_id") or "-"
    run_status = latest_run.get("status") or "-"
    request = _truncate(latest_run.get("request"), length=72)
    header = Table.grid(expand=True)
    header.add_column(ratio=1)
    header.add_column(ratio=5)
    header.add_row("Team", team_id)
    header.add_row("Run", str(run_id))
    header.add_row("State", Text(str(run_status), style=_status_style(str(run_status))))
    header.add_row("Request", request)
    return Group(Text(f"Alvis · {team_id}", style="bold bright_blue"), header)


def _text_body(body: RenderableType, *, tone: str) -> RenderableType:
    if isinstance(body, Text):
        body = body.copy()
        body.stylize(MESSAGE_TONES[tone][1])
        return body
    return body


def render_message(role: str, body: RenderableType, *, tone: str) -> RenderableType:
    header_style, _ = MESSAGE_TONES[tone]
    return Group(
        Text(role, style=header_style),
        Padding(_text_body(body, tone=tone), (0, 0, 0, 2)),
    )


def _event_summary(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return str(payload.get("summary") or payload.get("output_summary") or payload.get("detail") or event.get("event_type") or "-")


def _event_role(event: dict[str, Any], status: dict[str, Any]) -> str:
    agent_id = event.get("agent_id")
    if not agent_id:
        return "System"
    for agent in status.get("agents", []):
        if agent.get("agent_id") == agent_id:
            return str(agent.get("role_alias") or agent.get("role") or "worker").capitalize()
    return str(agent_id)


def should_render_event(event: dict[str, Any]) -> bool:
    return str(event.get("event_type") or "") in IMPORTANT_EVENT_TYPES


def _task_title_for_event(event: dict[str, Any], status: dict[str, Any]) -> str | None:
    task_id = event.get("task_id")
    if not task_id:
        return None
    task = _tasks_by_id(status).get(str(task_id))
    if not task:
        return None
    return str(task.get("title") or task.get("goal") or task_id)


def _worker_voice_message(event: dict[str, Any], status: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    event_type = str(event.get("event_type") or "")
    role = _event_role(event, status)
    task_title = _task_title_for_event(event, status)
    summary = _event_summary(event)

    if event_type == "task.assigned":
        if task_title:
            return f"{role}가 작업을 시작했습니다: {task_title}"
        return f"{role}가 작업을 시작했습니다."
    if event_type == "agent.output.delta":
        if summary == "No usable task output captured yet.":
            return ""
        return summary
    if event_type == "agent.output.final":
        parse_status = payload.get("output_parse_status")
        if parse_status in PARSE_STATUS_MESSAGES:
            return PARSE_STATUS_MESSAGES[str(parse_status)]
        if payload.get("status_signal") == "blocked" and summary:
            return summary
        if payload.get("status_signal") == "blocked":
            return f"작업이 막혔습니다: {summary}"
        return summary
    if event_type == "leader.output.ready":
        return "최종 응답 초안을 전달했습니다."
    if event_type == "interaction.created":
        if payload.get("interaction_kind") == "report_blocker":
            return "리더 입력이 필요합니다."
        return summary
    if event_type == "error.raised":
        detail = payload.get("error_summary") or payload.get("detail") or payload.get("reason")
        hint = payload.get("error_hint")
        exit_code = payload.get("exit_code")
        bits = [str(detail or summary)]
        if exit_code not in (None, ""):
            bits.append(f"exit={exit_code}")
        if hint:
            bits.append(str(hint))
        return " | ".join(bits)
    return summary


def render_event_message(event: dict[str, Any], status: dict[str, Any]) -> RenderableType:
    payload = event.get("payload") or {}
    detail = payload.get("message") or payload.get("detail")
    body = _worker_voice_message(event, status)
    if not body:
        body = _event_summary(event)
    if detail and detail != body:
        body = f"{body}\n{detail}"
    role = _event_role(event, status)
    event_type = str(event.get("event_type") or "")
    tone = "worker"
    if event_type == "error.raised":
        tone = "error"
    elif event_type == "interaction.created":
        tone = "warning"
    elif event_type == "leader.output.ready":
        tone = "output"
    elif role.lower() == "system":
        tone = "system"
    return render_message(role, Text(body), tone=tone)


def render_status_snapshot(status: dict[str, Any]) -> RenderableType:
    latest_run = status.get("latest_run") or {}
    lines = [
        f"run={latest_run.get('run_id') or '-'}",
        f"state={latest_run.get('status') or '-'}",
        f"request={latest_run.get('request') or '-'}",
    ]
    candidate = status.get("final_output_candidate") or {}
    if candidate.get("summary"):
        lines.append(f"final={candidate['summary']}")
    return render_message("Status", Text("\n".join(lines)), tone="status")


def render_logs_snapshot(events: list[dict[str, Any]], status: dict[str, Any]) -> RenderableType:
    visible = [event for event in events if should_render_event(event)][-8:]
    body = "\n".join(f"- {_event_role(event, status)}: {_worker_voice_message(event, status) or _event_summary(event)}" for event in visible) or "No recent events."
    return render_message("Logs", Text(body), tone="system")


@dataclass
class RequestHandle:
    thread: threading.Thread
    done: threading.Event
    error: Exception | None = None


def _worker_output_key(event: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    payload = event.get("payload") or {}
    return (
        str(event.get("event_type") or ""),
        str(event.get("agent_id") or ""),
        str(event.get("task_id") or ""),
        str(payload.get("summary") or payload.get("output_summary") or ""),
        str(payload.get("status_signal") or ""),
        str(payload.get("output_parse_status") or ""),
    )


def _worker_strip_signature(status: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    rows = []
    for agent in status.get("agents", []):
        if agent.get("role") == "leader":
            continue
        rows.append(
            (
                str(agent.get("role_alias") or agent.get("role") or "-"),
                str(agent.get("status") or "-"),
                _worker_task_summary(agent, status),
            )
        )
    return tuple(rows)


@dataclass
class ReplBackend:
    services: Any

    def status(self, team_id: str) -> dict[str, Any]:
        return self.services.status(team_id)

    def logs(self, team_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "agent_id": event.agent_id,
                "task_id": event.task_id,
                "payload": event.payload,
            }
            for event in self.services.list_events(team_id=team_id, run_id=run_id)
        ]

    def run_request(self, team_id: str, request: str) -> dict[str, Any]:
        from app.graph.supervisor import Supervisor, SupervisorDeps

        return Supervisor(SupervisorDeps(services=self.services)).run(team_id, request)

    def resume_run(self, run_id: str) -> dict[str, Any]:
        from app.graph.supervisor import Supervisor, SupervisorDeps

        return Supervisor(SupervisorDeps(services=self.services)).resume(run_id)

    def answer_interaction(self, team_id: str, answer: str) -> dict[str, Any]:
        return self.services.answer_pending_interaction(team_id, answer)

    def clean(self) -> dict[str, Any]:
        return self.services.clean_workspace_teams()

    def shutdown(self, team_id: str) -> dict[str, Any]:
        return self.services.remove_team(team_id)


def _sync_transcript(
    console: Console,
    *,
    status: dict[str, Any],
    events: list[dict[str, Any]],
    seen_event_ids: set[str],
    seen_worker_output_keys: set[tuple[str, str, str, str, str, str]],
    shown_final_keys: set[tuple[str, str]],
) -> None:
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen_event_ids or not should_render_event(event):
            continue
        if str(event.get("event_type") or "") in {"agent.output.delta", "agent.output.final"}:
            worker_key = _worker_output_key(event)
            if worker_key in seen_worker_output_keys:
                seen_event_ids.add(event_id)
                continue
            seen_worker_output_keys.add(worker_key)
        seen_event_ids.add(event_id)
        console.print(render_event_message(event, status))

    latest_run = status.get("latest_run") or {}
    final_response = latest_run.get("final_response")
    run_id = str(latest_run.get("run_id") or "")
    if final_response and run_id:
        final_key = (run_id, str(final_response))
        if final_key not in shown_final_keys:
            shown_final_keys.add(final_key)
            console.print(render_message("Alvis", Text(str(final_response)), tone="output"))


def _pending_banner(status: dict[str, Any]) -> str | None:
    pending = status.get("pending_interactions") or []
    if pending:
        return str(next((item.get("message") for item in pending if item.get("message")), "워커가 추가 입력을 기다리고 있습니다."))
    return None


def _print_prompt_context(console: Console, *, status: dict[str, Any]) -> None:
    console.print(render_worker_strip(status))
    question = _pending_banner(status)
    if question:
        console.print(render_message("Reply", Text(question), tone="warning"))


def _start_background_action(action) -> RequestHandle:
    done = threading.Event()
    handle = RequestHandle(thread=None, done=done)  # type: ignore[arg-type]

    def _target() -> None:
        try:
            action()
        except Exception as exc:  # pragma: no cover - exercised through launch flow
            handle.error = exc
        finally:
            done.set()

    thread = threading.Thread(target=_target, daemon=True)
    handle.thread = thread
    thread.start()
    return handle


def _monitor_request(
    console: Console,
    *,
    team_id: str,
    backend: ReplBackend,
    handle: RequestHandle,
    seen_event_ids: set[str],
    seen_worker_output_keys: set[tuple[str, str, str, str, str, str]],
    shown_final_keys: set[tuple[str, str]],
) -> None:
    last_worker_signature: tuple[tuple[str, str, str], ...] | None = None
    while not handle.done.wait(timeout=0.25):
        status = backend.status(team_id)
        latest_run = status.get("latest_run") or {}
        events = backend.logs(team_id, run_id=latest_run.get("run_id"))
        _sync_transcript(
            console,
            status=status,
            events=events,
            seen_event_ids=seen_event_ids,
            seen_worker_output_keys=seen_worker_output_keys,
            shown_final_keys=shown_final_keys,
        )
        worker_signature = _worker_strip_signature(status)
        if worker_signature != last_worker_signature:
            console.print(render_worker_strip(status))
            last_worker_signature = worker_signature
        time.sleep(0.05)
    handle.thread.join(timeout=0.1)
    status = backend.status(team_id)
    latest_run = status.get("latest_run") or {}
    events = backend.logs(team_id, run_id=latest_run.get("run_id"))
    _sync_transcript(
        console,
        status=status,
        events=events,
        seen_event_ids=seen_event_ids,
        seen_worker_output_keys=seen_worker_output_keys,
        shown_final_keys=shown_final_keys,
    )
    if handle.error is not None:
        console.print(render_message("System", Text(_friendly_background_error(handle.error)), tone="error"))


def _pending_question(status: dict[str, Any]) -> str | None:
    pending = status.get("pending_interactions") or []
    return next((item.get("message") for item in pending if item.get("message")), None)


def launch_repl(*, team_id: str, backend: ReplBackend) -> int:
    console = Console()
    seen_event_ids: set[str] = set()
    seen_worker_output_keys: set[tuple[str, str, str, str, str, str]] = set()
    shown_final_keys: set[tuple[str, str]] = set()

    status = backend.status(team_id)
    latest_run = status.get("latest_run") or {}
    run_id = latest_run.get("run_id")
    events = backend.logs(team_id, run_id=run_id)

    console.print(render_session_header(team_id, status))
    console.print(render_message("System", Text("세션이 준비되었습니다. 요청을 입력하면 결과가 아래로 계속 쌓입니다."), tone="system"))
    console.print(Text("/status  /logs  /clean  /quit  /shutdown", style="cyan"))
    console.print(render_worker_strip(status))
    _sync_transcript(
        console,
        status=status,
        events=events,
        seen_event_ids=seen_event_ids,
        seen_worker_output_keys=seen_worker_output_keys,
        shown_final_keys=shown_final_keys,
    )

    while True:
        status = backend.status(team_id)
        latest_run = status.get("latest_run") or {}
        run_id = latest_run.get("run_id")
        events = backend.logs(team_id, run_id=run_id)
        _sync_transcript(
            console,
            status=status,
            events=events,
            seen_event_ids=seen_event_ids,
            seen_worker_output_keys=seen_worker_output_keys,
            shown_final_keys=shown_final_keys,
        )
        _print_prompt_context(console, status=status)

        command = console.input("[bold cyan]> [/] ").strip()
        if not command:
            continue
        if command == "/quit":
            return 0
        if command == "/status":
            console.print(render_status_snapshot(status))
            continue
        if command == "/logs":
            console.print(render_logs_snapshot(events, status))
            continue
        if command == "/clean":
            console.print(render_message("System", Text(str(backend.clean())), tone="error"))
            return 0
        if command == "/shutdown":
            console.print(render_message("System", Text(str(backend.shutdown(team_id))), tone="error"))
            return 0

        console.print(render_message("You", Text(command), tone="user"))
        pending_question = _pending_question(status)
        if pending_question:
            result = backend.answer_interaction(team_id, command)
            run_id = result["run_id"]
            console.print(render_message("System", Text("질문에 답변했습니다. 후속 작업을 재개합니다."), tone="system"))
            handle = _start_background_action(lambda: backend.resume_run(run_id))
        else:
            console.print(render_message("System", Text("요청을 처리 중입니다. 워커 진행 상황을 아래에 계속 표시합니다."), tone="system"))
            handle = _start_background_action(lambda: backend.run_request(team_id, command))
        _monitor_request(
            console,
            team_id=team_id,
            backend=backend,
            handle=handle,
            seen_event_ids=seen_event_ids,
            seen_worker_output_keys=seen_worker_output_keys,
            shown_final_keys=shown_final_keys,
        )
