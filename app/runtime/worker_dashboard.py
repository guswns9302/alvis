from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import time
import traceback

from app.bootstrap import bootstrap_services
from app.runtime.output_collector import OutputCollector
from app.runtime.ui_state import WORKER_LOG_EVENTS, compact_task_title, filtered_events, format_timeline_entry, signal_dot, status_signal, worker_agents


HEADER_HEIGHT = 8


def _clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _rewrite_header(lines: list[str]) -> None:
    sys.stdout.write("\x1b[s")
    sys.stdout.write("\x1b[H")
    for row in range(HEADER_HEIGHT):
        sys.stdout.write("\x1b[2K")
        text = lines[row] if row < len(lines) else ""
        sys.stdout.write(text)
        if row < HEADER_HEIGHT - 1:
            sys.stdout.write("\n")
    sys.stdout.write("\x1b[u")
    sys.stdout.flush()


def _spawn_worker_runtime(agent_id: str) -> subprocess.Popen[str]:
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
    line = normalized.splitlines()[0].strip()
    if len(line) > 120:
        line = line[:117].rstrip() + "..."
    return line


def _header_lines(team_id: str, blink_on: bool) -> list[str]:
    services = bootstrap_services()
    status = services.status(team_id)
    workers = worker_agents(status)
    latest_run = status.get("latest_run") or {}
    lines = [
        f"Workers · {team_id}",
        f"요청: {latest_run.get('request') or '-'}",
        "상태:",
    ]
    for worker in workers:
        dot = signal_dot(status_signal(worker), blink_on and status_signal(worker) == "active")
        task_title = compact_task_title(status, worker)
        runtime = worker.get("runtime_health", {}).get("status") or "-"
        lines.append(
            f"  {dot} {worker.get('role_alias') or worker['role']:<10} "
            f"{worker['status']:<12} runtime={runtime:<10} task={task_title}"
        )
    lines.append("")
    lines.append("로그:")
    return lines[:HEADER_HEIGHT]


def _append_new_logs(team_id: str, seen_event_ids: set[str]) -> None:
    services = bootstrap_services()
    status = services.status(team_id)
    events = filtered_events(services, team_id, WORKER_LOG_EVENTS)
    for event in events:
        if event.event_id in seen_event_ids:
            continue
        seen_event_ids.add(event.event_id)
        line = format_timeline_entry(event, status)
        sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def _append_error(details: str) -> None:
    services = bootstrap_services()
    stderr_path = services.settings.log_dir / "worker-dashboard.stderr.log"
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    with stderr_path.open("a") as handle:
        handle.write(details)
        if not details.endswith("\n"):
            handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", required=True)
    args = parser.parse_args()
    team_id = args.team_id
    services = bootstrap_services()
    status = services.status(team_id)
    workers = worker_agents(status)
    processes = {worker["agent_id"]: _spawn_worker_runtime(worker["agent_id"]) for worker in workers}
    inbox_offsets = {worker["agent_id"]: 0 for worker in workers}
    seen_event_ids: set[str] = set()
    blink_on = True

    _clear()
    sys.stdout.write("\n" * (HEADER_HEIGHT - 1))
    sys.stdout.flush()

    try:
        while True:
            try:
                for agent_id, process in processes.items():
                    inbox_offsets[agent_id] = _pump_inbox(agent_id, process, inbox_offsets[agent_id])
                _rewrite_header(_header_lines(team_id, blink_on))
                _append_new_logs(team_id, seen_event_ids)
                blink_on = not blink_on
            except ValueError as exc:  # pragma: no cover - runtime cleanup path
                if "not found" in str(exc):
                    return 0
                raise
            except Exception:  # pragma: no cover - runtime UI fallback
                _append_error(traceback.format_exc())
                sys.stdout.write("[workers/error] dashboard render failed\n")
                sys.stdout.flush()
            select.select([], [], [], 0.8)
    finally:  # pragma: no cover - runtime cleanup
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
