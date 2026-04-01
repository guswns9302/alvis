from __future__ import annotations

import argparse
import json
import os
import select
import sys
import termios
import time
import traceback
import tty
from pathlib import Path

from app.bootstrap import bootstrap_services
from app.runtime.ui_state import LEADER_LOG_EVENTS, compact_task_title, filtered_events, format_timeline_entry, signal_dot, status_signal, worker_agents
from app.graph.supervisor import Supervisor, SupervisorDeps


TIMELINE_LIMIT = 12


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


def _parse_command(command: str) -> tuple[str, list[str]]:
    parts = command.strip().split()
    if not parts:
        return "", []
    return parts[0], parts[1:]


def _run_leader_command(team_id: str, command: str, supervisor: Supervisor) -> str | None:
    action, _args = _parse_command(command)
    if action in {"/refresh", "/status"}:
        return None
    supervisor.run(team_id, command)
    return None


def _timeline(status: dict, services, team_id: str) -> list[str]:
    events = filtered_events(services, team_id, LEADER_LOG_EVENTS)[-TIMELINE_LIMIT:]
    return [format_timeline_entry(event, status) for event in events]


def _worker_status_lines(status: dict, blink_on: bool) -> list[str]:
    lines = []
    for worker in worker_agents(status):
        dot = signal_dot(status_signal(worker), blink_on and status_signal(worker) == "active")
        lines.append(
            f"  {dot} {worker.get('role_alias') or worker['role']:<10} "
            f"{worker['status']:<12} {compact_task_title(status, worker, width=42)}"
        )
    return lines or ["  - 워커 없음"]


def _render_buffer(team_id: str, command_buffer: str, blink_on: bool) -> str:
    services = bootstrap_services()
    _write_ready_state(team_id)
    status = services.status(team_id)
    latest_run = status.get("latest_run") or {}
    lines: list[str] = [f"Alvis Leader Console · {team_id}", ""]
    lines.append(f"최근 실행: {latest_run.get('run_id') or '없음'} · {latest_run.get('status') or '-'}")
    lines.append(f"요청: {latest_run.get('request') or '-'}")
    lines.append("")
    lines.append("현재 작업 상태:")
    redo_tasks = status.get("redo_tasks", [])
    handoffs = status.get("handoffs", [])
    if redo_tasks:
        lines.append(f"  재작업 대기: {len(redo_tasks)}")
    elif handoffs:
        lines.append(f"  자동 handoff: {len(handoffs)}")
    else:
        lines.append("  진행 중인 handoff 없음")
    lines.append("")
    lines.append("워커 상태:")
    lines.extend(_worker_status_lines(status, blink_on))
    lines.append("")
    lines.append("작업 로그:")
    for entry in _timeline(status, services, team_id):
        lines.append(f"  {entry}")
    if not status.get("latest_run"):
        lines.append("  [system] 아직 실행된 run이 없습니다.")
    lines.append("")
    lines.append("최종 결과:")
    candidate = status.get("final_output_candidate")
    if status.get("final_output_ready") and latest_run.get("final_response"):
        lines.append(f"  {latest_run['final_response']}")
    elif candidate:
        lines.append(f"  후보: {candidate.get('summary') or '-'}")
    else:
        lines.append("  아직 없음")
    lines.append("")
    lines.append("입력: 새 요청 또는 /refresh /status /quit")
    lines.append(f"> {command_buffer}")
    return "\n".join(lines)


def _read_command(buffer: str) -> tuple[str, str | None]:
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    if not ready:
        return buffer, None
    chunk = os.read(sys.stdin.fileno(), 32).decode(errors="ignore")
    command = None
    for char in chunk:
        if char in {"\r", "\n"}:
            command = buffer.strip()
            buffer = ""
        elif char == "\x7f":
            buffer = buffer[:-1]
        elif char == "\x03":
            raise KeyboardInterrupt
        elif char == "\x18":
            command = "/shutdown"
            buffer = ""
        elif char.isprintable():
            buffer += char
    return buffer, command


def _render_prompt_line(command_buffer: str) -> None:
    sys.stdout.write("\x1b[s")
    sys.stdout.write("\x1b[999;1H")
    sys.stdout.write("\x1b[2K")
    sys.stdout.write(f"> {command_buffer}")
    sys.stdout.write("\x1b[u")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", required=True)
    args = parser.parse_args()
    team_id = args.team_id
    supervisor = Supervisor(SupervisorDeps(services=bootstrap_services()))
    last_render = ""
    command_feedback = ""
    command_buffer = ""
    blink_on = True
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    try:
        while True:
            try:
                current = _render_buffer(team_id, command_buffer, blink_on)
                blink_on = not blink_on
                if current != last_render:
                    _clear()
                    print(current, end="", flush=True)
                    if command_feedback:
                        print(f"\n{command_feedback}", end="", flush=True)
                    last_render = current
                command_buffer, command = _read_command(command_buffer)
                _render_prompt_line(command_buffer)
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
            if command is not None:
                if command == "/shutdown":
                    services = bootstrap_services()
                    services.shutdown_tmux_team(team_id)
                    return 0
                if command == "/quit":
                    return 0
                if command:
                    try:
                        result = _run_leader_command(team_id, command, supervisor)
                        command_feedback = result or ""
                        last_render = ""
                    except Exception as exc:  # pragma: no cover - runtime UI fallback
                        _write_state(team_id, status="error", mode="leader_console", reason="run_failed")
                        _append_error(team_id, traceback.format_exc())
                        command_feedback = f"[ERROR] {exc}"
                        last_render = ""
            time.sleep(0.05)
    finally:  # pragma: no cover - terminal restore
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


if __name__ == "__main__":
    raise SystemExit(main())
