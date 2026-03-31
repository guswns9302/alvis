from __future__ import annotations

import argparse
import json
import os
import pty
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path


def write_state(path: Path, **payload) -> None:
    path.write_text(json.dumps(payload))


def _drain_master(master_fd: int, stdout_handle) -> bool:
    try:
        chunk = os.read(master_fd, 4096)
    except OSError:
        return False
    if not chunk:
        return False
    stdout_handle.write(chunk.decode("utf-8", errors="ignore"))
    stdout_handle.flush()
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--heartbeat-file", required=True)
    parser.add_argument("--stdout-file", required=True)
    parser.add_argument("--stderr-file", required=True)
    parser.add_argument("--state-file", required=True)
    args = parser.parse_args()

    heartbeat_file = Path(args.heartbeat_file)
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file = Path(args.stderr_file)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    stdout_file = Path(args.stdout_file)
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    state_file = Path(args.state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    write_state(state_file, status="starting", cwd=args.cwd, codex_command=args.codex_command)
    with stdout_file.open("a", encoding="utf-8", errors="ignore") as stdout_handle, stderr_file.open("a", encoding="utf-8", errors="ignore") as stderr_handle:
        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                shlex.split(args.codex_command),
                cwd=args.cwd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=stderr_handle,
            )
        except FileNotFoundError as exc:
            os.close(master_fd)
            os.close(slave_fd)
            write_state(state_file, status="error", reason="spawn_failed", detail=str(exc))
            return 1
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        write_state(state_file, status="ready", cwd=args.cwd, codex_command=args.codex_command, pid=process.pid)
        try:
            while process.poll() is None:
                readable, _, _ = select.select([master_fd, sys.stdin], [], [], 1)
                if master_fd in readable:
                    _drain_master(master_fd, stdout_handle)
                if sys.stdin in readable:
                    incoming = os.read(sys.stdin.fileno(), 4096)
                    if incoming:
                        os.write(master_fd, incoming)
                heartbeat_file.write_text(json.dumps({"heartbeat_at": time.time()}))
            while _drain_master(master_fd, stdout_handle):
                pass
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            write_state(
                state_file,
                status="exited",
                cwd=args.cwd,
                codex_command=args.codex_command,
                pid=process.pid,
                exit_code=process.returncode,
            )
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
