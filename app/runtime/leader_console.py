from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from app.bootstrap import bootstrap_services
from app.enums import EventType
from app.graph.supervisor import Supervisor, SupervisorDeps


def _clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _paths(team_id: str) -> dict[str, Path]:
    services = bootstrap_services()
    return services.codex.session_paths(f"{team_id}-leader")


def _write_state(team_id: str, **payload) -> None:
    _paths(team_id)["state"].write_text(json.dumps(payload))


def _write_heartbeat(team_id: str) -> None:
    _paths(team_id)["heartbeat"].write_text(json.dumps({"heartbeat_at": time.time()}))


def _write_ready_state(team_id: str) -> None:
    _write_state(team_id, status="ready", mode="leader_console")
    _write_heartbeat(team_id)


def _append_error(team_id: str, details: str) -> None:
    with _paths(team_id)["stderr"].open("a") as handle:
        handle.write(details)
        if not details.endswith("\n"):
            handle.write("\n")


def _recent_messages(services, team_id: str, limit: int = 10) -> list[str]:
    visible = {
        EventType.RUN_CREATED.value,
        EventType.TASK_CREATED.value,
        EventType.TASK_ASSIGNED.value,
        EventType.TASK_HANDOFF_CREATED.value,
        EventType.TASK_HANDOFF_DISPATCHED.value,
        EventType.LEADER_OUTPUT_READY.value,
        EventType.INTERACTION_CREATED.value,
        EventType.INTERACTION_ROUTED.value,
        EventType.INTERACTION_RESOLVED.value,
        EventType.LEADER_INSTRUCTION_CREATED.value,
        EventType.ERROR_RAISED.value,
    }
    events = [event for event in services.list_events(team_id=team_id) if event.event_type in visible][-limit:]
    lines = []
    for event in events:
        summary = event.payload.get("summary", event.event_type)
        prefix = event.agent_id or "system"
        lines.append(f"- [{prefix}] {summary}")
    return lines


def _render_handoffs(status: dict) -> list[str]:
    handoffs = status.get("handoffs", [])
    lines = [f"자동 handoff: {len(handoffs)}"]
    for item in handoffs[:5]:
        lines.append(
            f"  - {item['task_id']} role={item.get('target_role_alias') or '-'} "
            f"status={item.get('status') or '-'} title={item.get('title') or '-'}"
        )
    return lines


def _parse_command(command: str) -> tuple[str, list[str]]:
    parts = command.strip().split()
    if not parts:
        return "", []
    return parts[0], parts[1:]


def _run_leader_command(team_id: str, command: str, supervisor: Supervisor) -> str | None:
    action, args = _parse_command(command)
    if action in {"/refresh", "/status"}:
        return None
    supervisor.run(team_id, command)
    return None


def _render_buffer(team_id: str) -> str:
    services = bootstrap_services()
    _write_ready_state(team_id)
    status = services.status(team_id)
    lines: list[str] = [f"Alvis Leader Console · {team_id}", ""]
    latest_run = status.get("latest_run")
    if latest_run:
        lines.append(f"최근 실행: {latest_run['run_id']} · {latest_run['status']}")
        lines.append(f"요청: {latest_run['request']}")
        if latest_run.get("final_response") and status.get("final_output_ready", False):
            lines.append(f"최종 응답: {latest_run['final_response']}")
        elif latest_run.get("final_response"):
            lines.append(f"진행 상태: {latest_run['final_response']}")
    else:
        lines.append("최근 실행: 없음")
    lines.append("")
    lines.extend(_render_handoffs(status))
    lines.append("")
    lines.append(f"리더 큐: {len(status.get('leader_queue', []))}")
    for item in status.get("leader_queue", [])[:5]:
        lines.append(f"  - {item['kind']}: {item.get('message') or '-'}")
    lines.append("")
    lines.append("최근 메시지:")
    for line in _recent_messages(services, team_id):
        lines.append(f"  {line}")
    lines.append("")
    candidate = status.get("final_output_candidate")
    if candidate:
        readiness = "ready" if status.get("final_output_ready") else "not-ready"
        lines.append(f"최종 출력 후보 ({readiness}): {candidate.get('summary') or '-'}")
        redo_tasks = status.get("redo_tasks", [])
        if redo_tasks:
            lines.append(f"재작업 작업: {len(redo_tasks)}")
            for item in redo_tasks[:3]:
                lines.append(
                    f"  - {item['task_id']} role={item.get('target_role_alias') or '-'} "
                    f"status={item.get('status') or '-'} title={item.get('title') or '-'}"
                )
        lines.append("")
    lines.append("입력: 새 요청 또는 /refresh /status /quit")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", required=True)
    args = parser.parse_args()
    team_id = args.team_id
    supervisor = Supervisor(SupervisorDeps(services=bootstrap_services()))
    last_render = ""
    command_feedback = ""

    while True:
        try:
            current = _render_buffer(team_id)
            if current != last_render:
                _clear()
                print(current)
                if command_feedback:
                    print()
                    print(command_feedback)
                last_render = current
            print("> ", end="", flush=True)
        except ValueError as exc:  # pragma: no cover - runtime cleanup path
            if "not found" in str(exc):
                return 0
            raise
        except Exception:  # pragma: no cover - runtime UI fallback
            _write_state(team_id, status="error", mode="leader_console", reason="render_failed")
            _append_error(team_id, traceback.format_exc())
            _clear()
            print(f"Alvis Leader Console · {team_id}")
            print()
            print("리더 콘솔을 렌더링하는 중 오류가 발생했습니다. 재시도 중입니다.")
            print(traceback.format_exc().strip().splitlines()[-1])
            time.sleep(1)
            continue
        try:
            command = input().strip()
        except EOFError:
            _write_ready_state(team_id)
            time.sleep(1)
            continue
        if not command:
            continue
        if command == "/quit":
            return 0
        try:
            result = _run_leader_command(team_id, command, supervisor)
            command_feedback = result or ""
            last_render = ""
        except Exception as exc:  # pragma: no cover - runtime UI fallback
            _write_state(team_id, status="error", mode="leader_console", reason="run_failed")
            _append_error(team_id, traceback.format_exc())
            print(f"\n[ERROR] {exc}")
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
