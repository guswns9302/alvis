from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

from app.bootstrap import bootstrap_services
from app.enums import EventType
from app.runtime.output_collector import OutputCollector


def _clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _relevant_messages(services, team_id: str, agent_id: str, limit: int = 12) -> list[str]:
    visible = {
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
    events = [event for event in services.list_events(team_id=team_id) if event.event_type in visible]
    lines = []
    for event in events:
        if event.agent_id not in {None, agent_id} and event.task_id is None:
            continue
        summary = event.payload.get("summary", event.event_type)
        if event.agent_id and event.agent_id != agent_id and "leader" not in (event.agent_id or ""):
            continue
        lines.append(f"- [{event.agent_id or 'system'}] {summary}")
    return lines[-limit:]


def _spawn_worker_runtime(team_id: str, agent_id: str) -> subprocess.Popen[str]:
    services = bootstrap_services()
    paths = services.codex.session_paths(agent_id)
    paths["inbox"].write_text("")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.runtime.codex_session_wrapper",
            "--cwd",
            str(services.settings.repo_root),
            "--codex-command",
            services.settings.codex_command,
            "--heartbeat-file",
            str(paths["heartbeat"]),
            "--stdout-file",
            str(paths["stdout"]),
            "--stderr-file",
            str(paths["stderr"]),
            "--state-file",
            str(paths["state"]),
        ],
        stdin=subprocess.PIPE,
        text=True,
    )


def _append_error(agent_id: str, details: str) -> None:
    services = bootstrap_services()
    stderr_path = services.codex.session_paths(agent_id)["stderr"]
    with stderr_path.open("a") as handle:
        handle.write(details)
        if not details.endswith("\n"):
            handle.write("\n")


def _pump_inbox(agent_id: str, process: subprocess.Popen[str], offset: int) -> int:
    services = bootstrap_services()
    inbox_path = services.codex.session_paths(agent_id)["inbox"]
    if not inbox_path.exists():
        return offset
    lines = inbox_path.read_text().splitlines()
    if offset >= len(lines):
        return offset
    for raw in lines[offset:]:
        if not raw.strip():
            continue
        payload = json.loads(raw)
        if process.stdin:
            process.stdin.write(payload["prompt"] + "\n")
            process.stdin.flush()
    return len(lines)


def _clean_summary(summary: str | None) -> str | None:
    if not summary:
        return None
    collector = OutputCollector()
    normalized = collector._normalize_text(summary)  # type: ignore[attr-defined]
    if not normalized:
        return None
    first_line = normalized.splitlines()[0].strip()
    if len(first_line) > 180:
        first_line = first_line[:177].rstrip() + "..."
    return first_line


def _format_output_section(output: dict | None, *, limit: int = 3) -> list[str]:
    if not output:
        return ["마지막 결과: 없음"]
    lines = ["마지막 결과:"]
    status_signal = output.get("status_signal")
    if status_signal:
        lines.append(f"  상태 신호: {status_signal}")
    summary = _clean_summary(output.get("summary"))
    if summary:
        lines.append(f"  요약: {summary}")
    sections = [
        ("변경 파일", output.get("changed_files", [])),
        ("테스트", output.get("test_results", [])),
        ("리스크", output.get("risk_flags", [])),
        ("리더 질문", output.get("question_for_leader", [])),
        ("필요 컨텍스트", output.get("requested_context", [])),
        ("후속 제안", output.get("followup_suggestion", [])),
        ("의존성 메모", output.get("dependency_note", [])),
    ]
    for title, items in sections:
        if not items:
            continue
        visible = items[:limit]
        lines.append(f"  {title}:")
        for item in visible:
            cleaned = _clean_summary(item) or item.strip()
            lines.append(f"    - {cleaned}")
        if len(items) > limit:
            lines.append(f"    - ... 외 {len(items) - limit}개")
    return lines


def _render_buffer(team_id: str, agent_id: str) -> str:
    services = bootstrap_services()
    status = services.status(team_id)
    agent = next(item for item in status["agents"] if item["agent_id"] == agent_id)
    lines: list[str] = [f"{agent_id} · {agent.get('role_alias') or agent['role']}", ""]
    lines.append(f"상태: {agent['status']} · runtime={agent['runtime_health']['status']}")
    current_task_id = agent.get("task")
    if current_task_id:
        task = next((item for item in status["tasks"] if item["task_id"] == current_task_id), None)
        if task:
            lines.append(f"현재 작업: {task['title']}")
            if agent["role"] == "reviewer" and task["title"] == "Validate and summarize":
                lines.append("목표: 이전 worker 산출물을 검토하고 최종 답변 가능 여부를 판단")
            else:
                lines.append(f"목표: {task['goal']}")
            lines.append(f"경로: {', '.join(task.get('owned_paths', [])) or '-'}")
            lines.extend(_format_output_section(task.get("latest_output")))
    else:
        lines.append("현재 작업: 없음")
    lines.append("")
    lines.append("최근 메시지:")
    for line in _relevant_messages(services, team_id, agent_id):
        lines.append(f"  {line}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--agent-id", required=True)
    args = parser.parse_args()
    team_id = args.team_id
    agent_id = args.agent_id
    process = _spawn_worker_runtime(team_id, agent_id)
    inbox_offset = 0
    last_render = ""
    try:
        while True:
            try:
                inbox_offset = _pump_inbox(agent_id, process, inbox_offset)
                current = _render_buffer(team_id, agent_id)
                if current != last_render:
                    _clear()
                    print(current)
                    last_render = current
            except ValueError as exc:  # pragma: no cover - runtime cleanup path
                if "not found" in str(exc):
                    return 0
                raise
            except Exception:  # pragma: no cover - runtime UI fallback
                _append_error(agent_id, traceback.format_exc())
                _clear()
                print(f"{agent_id} · monitor error")
                print()
                print("워커 모니터를 갱신하는 중 오류가 발생했습니다. 재시도 중입니다.")
                print(traceback.format_exc().strip().splitlines()[-1])
            time.sleep(1)
    finally:  # pragma: no cover - runtime cleanup
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
