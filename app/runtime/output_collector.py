from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.schemas import AgentOutput


@dataclass
class OutputSnapshot:
    heartbeat_at: float | None
    log_text: str


class OutputCollector:
    ANSI_PATTERN = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\))")

    def read_snapshot(self, stdout_log: str | Path | None, heartbeat_file: str | Path | None) -> OutputSnapshot:
        heartbeat_at = None
        if heartbeat_file:
            heartbeat_path = Path(heartbeat_file)
            if heartbeat_path.exists():
                try:
                    heartbeat_at = json.loads(heartbeat_path.read_text()).get("heartbeat_at")
                except json.JSONDecodeError:
                    heartbeat_at = None
        log_text = ""
        if stdout_log:
            stdout_path = Path(stdout_log)
            if stdout_path.exists():
                log_text = stdout_path.read_text()
        return OutputSnapshot(heartbeat_at=heartbeat_at, log_text=log_text)

    def summarize_task_output(self, *, agent_id: str, task_id: str, log_text: str) -> AgentOutput:
        clean_text = self.ANSI_PATTERN.sub("", log_text)
        lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
        relevant = lines[-10:]
        changed_files = []
        test_results = []
        risk_flags = []
        for line in relevant:
            lower = line.lower()
            if "test" in lower:
                test_results.append(line)
            if "error" in lower or "failed" in lower:
                risk_flags.append(line)
            if line.startswith(("M ", "A ", "D ")):
                changed_files.append(line)
        summary = relevant[-1] if relevant else "No task output captured yet."
        kind = "final" if relevant else "delta"
        return AgentOutput(
            task_id=task_id,
            agent_id=agent_id,
            kind=kind,
            summary=summary,
            changed_files=changed_files,
            test_results=test_results,
            risk_flags=risk_flags,
        )
