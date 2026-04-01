from __future__ import annotations

import argparse
import json
import select
import subprocess
import sys
import traceback

from app.bootstrap import bootstrap_services
from app.runtime.ui_state import WORKER_LOG_EVENTS, filtered_events, format_timeline_entry, worker_agents


def _clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
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

def _coalesce_log_line(line: str, counts: dict[str, int]) -> str | None:
    if counts.get(line):
        counts[line] += 1
        return None
    counts.clear()
    counts[line] = 1
    return line


def _append_new_logs(team_id: str, seen_event_ids: set[str], repeated_lines: dict[str, int]) -> None:
    services = bootstrap_services()
    status = services.status(team_id)
    events = filtered_events(services, team_id, WORKER_LOG_EVENTS)
    for event in events:
        if event.event_id in seen_event_ids:
            continue
        seen_event_ids.add(event.event_id)
        line = format_timeline_entry(event, status)
        rendered = _coalesce_log_line(line, repeated_lines)
        if rendered is None:
            continue
        sys.stdout.write(f"{rendered}\n")
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
    repeated_lines: dict[str, int] = {}

    _clear()
    sys.stdout.write(f"Workers · {team_id}\n")
    sys.stdout.write("로그:\n")
    sys.stdout.flush()

    try:
        while True:
            try:
                for agent_id, process in processes.items():
                    inbox_offsets[agent_id] = _pump_inbox(agent_id, process, inbox_offsets[agent_id])
                _append_new_logs(team_id, seen_event_ids, repeated_lines)
            except ValueError as exc:  # pragma: no cover - runtime cleanup path
                if "not found" in str(exc):
                    return 0
                raise
            except Exception:  # pragma: no cover - runtime UI fallback
                _append_error(traceback.format_exc())
                sys.stdout.write("[workers/error] dashboard render failed\n")
                sys.stdout.flush()
            select.select([], [], [], 0.2)
    finally:  # pragma: no cover - runtime cleanup
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
