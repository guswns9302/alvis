from __future__ import annotations

import argparse
import json
import os
import pty
import shlex
import subprocess
import threading
import time
from pathlib import Path

from app.runtime.output_collector import OutputCollector


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _build_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "status_signal": {"type": "string", "enum": sorted(OutputCollector.VALID_STATUS_SIGNALS)},
            "summary": {"type": "string"},
            "question_for_leader": {"type": "array", "items": {"type": "string"}},
            "requested_context": {"type": "array", "items": {"type": "string"}},
            "followup_suggestion": {"type": "array", "items": {"type": "string"}},
            "dependency_note": {"type": "array", "items": {"type": "string"}},
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "test_results": {"type": "array", "items": {"type": "string"}},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "status_signal",
            "summary",
            "question_for_leader",
            "requested_context",
            "followup_suggestion",
            "dependency_note",
            "changed_files",
            "test_results",
            "risk_flags",
        ],
        "additionalProperties": False,
    }


def _build_invocation(command_text: str, schema_path: Path, schema_output_path: Path, last_message_path: Path) -> list[str]:
    command = shlex.split(command_text)
    if not command:
        return ["codex", "exec", "--color", "never"]
    executable = Path(command[0]).name
    if executable == "codex" and "exec" not in command[1:]:
        command = [*command, "exec", "--color", "never"]
    if executable != "codex" or "exec" not in command[1:]:
        return command
    invocation = list(command)
    if invocation and invocation[-1] == "-":
        invocation = invocation[:-1]
    if "--skip-git-repo-check" not in invocation:
        invocation.append("--skip-git-repo-check")
    if "--output-schema" not in invocation:
        invocation.extend(["--output-schema", str(schema_path)])
    if "-o" not in invocation and "--output-last-message" not in invocation:
        invocation.extend(["-o", str(schema_output_path)])
    return invocation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--codex-command", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--heartbeat-file", required=True)
    parser.add_argument("--stdout-file", required=True)
    parser.add_argument("--stderr-file", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--schema-output-file", required=True)
    parser.add_argument("--last-message-file", required=True)
    args = parser.parse_args()

    cwd = Path(args.cwd)
    prompt_file = Path(args.prompt_file)
    heartbeat_file = Path(args.heartbeat_file)
    stdout_file = Path(args.stdout_file)
    stderr_file = Path(args.stderr_file)
    state_file = Path(args.state_file)
    schema_output_file = Path(args.schema_output_file)
    last_message_file = Path(args.last_message_file)
    schema_path = state_file.parent / "task_output_schema.json"
    schema_path.write_text(json.dumps(_build_schema(), ensure_ascii=False), encoding="utf-8")

    prompt_text = prompt_file.read_text(encoding="utf-8")
    command = _build_invocation(args.codex_command, schema_path, schema_output_file, last_message_file)
    _write_json(state_file, {"status": "starting", "cwd": str(cwd), "command": command, "output_collected": False})

    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdin=slave_fd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    os.close(slave_fd)
    _write_json(
        state_file,
        {
            "status": "running",
            "cwd": str(cwd),
            "command": command,
            "pid": process.pid,
            "output_collected": False,
            "started_at": time.time(),
        },
    )

    captured: dict[str, str | None] = {"stdout": None, "stderr": None}

    def _communicate() -> None:
        os.write(master_fd, prompt_text.encode("utf-8", errors="ignore") + b"\n\x04")
        stdout, stderr = process.communicate()
        os.close(master_fd)
        captured["stdout"] = stdout
        captured["stderr"] = stderr

    thread = threading.Thread(target=_communicate, daemon=True)
    thread.start()
    while thread.is_alive():
        _write_json(heartbeat_file, {"heartbeat_at": time.time()})
        thread.join(timeout=0.25)

    stdout_file.write_text(captured["stdout"] or "", encoding="utf-8")
    stderr_file.write_text(captured["stderr"] or "", encoding="utf-8")
    _write_json(heartbeat_file, {"heartbeat_at": time.time()})
    _write_json(
        state_file,
        {
            "status": "exited",
            "cwd": str(cwd),
            "command": command,
            "pid": process.pid,
            "exit_code": process.returncode,
            "output_collected": False,
            "finished_at": time.time(),
        },
    )
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
