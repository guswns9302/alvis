from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import time
from pathlib import Path

from app.runtime.output_collector import OutputCollector
from app.runtime.sdk_worker import run_sdk_worker
from app.schemas import TaskContract


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


def _is_codex_exec_command(command: list[str]) -> bool:
    return bool(command) and Path(command[0]).name == "codex" and "exec" in command[1:]


def _run_codex_exec(command: list[str], prompt_text: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*command, prompt_text],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _run_generic_command(command: list[str], prompt_text: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        input=prompt_text,
        capture_output=True,
        text=True,
    )


def _run_command_backend(
    *,
    command_text: str,
    prompt_text: str,
    cwd: Path,
    schema_path: Path,
    schema_output_file: Path,
    last_message_file: Path,
) -> subprocess.CompletedProcess[str]:
    command = _build_invocation(command_text, schema_path, schema_output_file, last_message_file)
    if _is_codex_exec_command(command):
        return _run_codex_exec(command, prompt_text, cwd)
    return _run_generic_command(command, prompt_text, cwd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--backend", default="sdk")
    parser.add_argument("--codex-command", required=True)
    parser.add_argument("--worker-model", default="gpt-5.4")
    parser.add_argument("--worker-reasoning-effort", default="medium")
    parser.add_argument("--worker-timeout-seconds", type=int, default=180)
    parser.add_argument("--worker-max-tool-rounds", type=int, default=12)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--contract-file", required=True)
    parser.add_argument("--heartbeat-file", required=True)
    parser.add_argument("--stdout-file", required=True)
    parser.add_argument("--stderr-file", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--schema-output-file", required=True)
    parser.add_argument("--last-message-file", required=True)
    args = parser.parse_args()

    cwd = Path(args.cwd)
    prompt_file = Path(args.prompt_file)
    contract_file = Path(args.contract_file)
    heartbeat_file = Path(args.heartbeat_file)
    stdout_file = Path(args.stdout_file)
    stderr_file = Path(args.stderr_file)
    state_file = Path(args.state_file)
    schema_output_file = Path(args.schema_output_file)
    last_message_file = Path(args.last_message_file)
    schema_path = state_file.parent / "task_output_schema.json"
    schema_path.write_text(json.dumps(_build_schema(), ensure_ascii=False), encoding="utf-8")

    prompt_text = prompt_file.read_text(encoding="utf-8")
    contract = TaskContract.model_validate_json(contract_file.read_text(encoding="utf-8"))
    _write_json(
        state_file,
        {
            "status": "starting",
            "cwd": str(cwd),
            "backend": args.backend,
            "command": args.codex_command,
            "output_collected": False,
        },
    )

    started_at = time.time()
    _write_json(
        state_file,
        {
            "status": "running",
            "cwd": str(cwd),
            "backend": args.backend,
            "command": args.codex_command,
            "output_collected": False,
            "started_at": started_at,
        },
    )
    exit_code = 0
    stdout_text = ""
    stderr_text = ""
    try:
        if args.backend == "sdk":
            output, final_text = run_sdk_worker(
                prompt_text=prompt_text,
                contract=contract,
                agent_id=args.agent_id,
                api_key=None,
                model=args.worker_model,
                reasoning_effort=args.worker_reasoning_effort,
                max_tool_rounds=args.worker_max_tool_rounds,
                timeout_seconds=args.worker_timeout_seconds,
            )
            schema_output_file.write_text(output.model_dump_json(), encoding="utf-8")
            last_message_file.write_text(final_text, encoding="utf-8")
            stdout_text = f"SDK worker completed for {contract.task_id}\n"
        else:
            completed = _run_command_backend(
                command_text=args.codex_command,
                prompt_text=prompt_text,
                cwd=cwd,
                schema_path=schema_path,
                schema_output_file=schema_output_file,
                last_message_file=last_message_file,
            )
            exit_code = completed.returncode or 0
            stdout_text = completed.stdout or ""
            stderr_text = completed.stderr or ""
        heartbeat_file.write_text(json.dumps({"heartbeat_at": time.time()}), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - runtime path
        exit_code = 1
        stderr_text = str(exc)

    stdout_file.write_text(stdout_text, encoding="utf-8")
    stderr_file.write_text(stderr_text, encoding="utf-8")
    _write_json(
        state_file,
        {
            "status": "exited",
            "cwd": str(cwd),
            "backend": args.backend,
            "command": args.codex_command,
            "exit_code": exit_code,
            "output_collected": False,
            "finished_at": time.time(),
        },
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
