from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--heartbeat-file", required=True)
    parser.add_argument("--stderr-file", required=True)
    args = parser.parse_args()

    heartbeat_file = Path(args.heartbeat_file)
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file = Path(args.stderr_file)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    print("[ALVIS SESSION START]", flush=True)
    with stderr_file.open("a") as stderr_handle:
        process = subprocess.Popen([args.codex_command], cwd=args.cwd, stderr=stderr_handle)
        try:
            while process.poll() is None:
                heartbeat_file.write_text(json.dumps({"heartbeat_at": time.time()}))
                time.sleep(5)
        finally:
            print("[ALVIS SESSION EXIT]", flush=True)
    return process.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
