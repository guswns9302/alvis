from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.logging import get_logger
from app.schemas import TaskContract
from app.sessions.tmux_manager import TmuxManager


class CodexAdapter:
    def __init__(self, tmux: TmuxManager, codex_command: str, log_dir: Path):
        self.tmux = tmux
        self.codex_command = codex_command
        self.log_dir = log_dir
        self.log = get_logger(__name__)

    def build_task_prompt(self, contract: TaskContract) -> str:
        constraints = "\n".join(f"- {item}" for item in contract.constraints) or "- None"
        expected = "\n".join(f"- {item}" for item in contract.expected_output) or "- Summary"
        context_lines = "\n".join(f"- {key}: {value}" for key, value in contract.context.items()) or "- None"
        return (
            "[ALVIS TASK]\n"
            f"task_id: {contract.task_id}\n"
            f"role: {contract.role}\n"
            f"cwd: {contract.cwd}\n"
            f"goal: {contract.goal}\n"
            "constraints:\n"
            f"{constraints}\n"
            "expected_output:\n"
            f"{expected}\n"
            f"completion_rule: {contract.completion_rule}\n"
            "context:\n"
            f"{context_lines}\n"
        )

    def build_bootstrap_command(self, cwd: str) -> str:
        log_file = self.log_dir / f"codex-session-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.log"
        return (
            f"cd {cwd}\n"
            f"printf '[ALVIS SESSION START] %s\\n' \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" | tee -a {log_file}\n"
            f"{self.codex_command}\n"
        )

    def bootstrap_session(self, pane_id: str, cwd: str) -> None:
        self.tmux.send_input(pane_id, self.build_bootstrap_command(cwd))

    def dispatch_task(self, pane_id: str, contract: TaskContract) -> str:
        prompt = self.build_task_prompt(contract)
        self.log.info("codex.dispatch", pane_id=pane_id, task_id=contract.task_id)
        self.tmux.send_input(pane_id, prompt)
        return prompt
