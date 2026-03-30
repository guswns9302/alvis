from __future__ import annotations

from pathlib import Path

from app.logging import get_logger
from app.schemas import TaskContract
from app.sessions.tmux_manager import TmuxManager


class CodexAdapter:
    def __init__(self, tmux: TmuxManager, codex_command: str, log_dir: Path, repo_root: Path, runtime_dir: Path):
        self.tmux = tmux
        self.codex_command = codex_command
        self.log_dir = log_dir
        self.repo_root = repo_root
        self.runtime_dir = runtime_dir
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
            "result_template:\n"
            "ALVIS_RESULT_START\n"
            "SUMMARY: <one concise summary line>\n"
            "CHANGED_FILES:\n"
            "- <path or file summary>\n"
            "TEST_RESULTS:\n"
            "- <test result>\n"
            "RISK_FLAGS:\n"
            "- <risk or blocker>\n"
            "ALVIS_RESULT_END\n"
            "context:\n"
            f"{context_lines}\n"
        )

    def session_paths(self, agent_id: str) -> dict[str, Path]:
        agent_dir = self.runtime_dir / "agents" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return {
            "dir": agent_dir,
            "heartbeat": agent_dir / "heartbeat.json",
            "stdout": agent_dir / "pane.log",
            "stderr": agent_dir / "stderr.log",
        }

    def build_bootstrap_command(self, agent_id: str, cwd: str) -> str:
        paths = self.session_paths(agent_id)
        wrapper = self.repo_root / "scripts" / "codex_session_wrapper.py"
        return (
            f"cd {cwd}\n"
            "export PYTHONUNBUFFERED=1\n"
            f"python3 {wrapper} "
            f"--cwd {cwd} "
            f"--codex-command {self.codex_command} "
            f"--heartbeat-file {paths['heartbeat']} "
            f"--stderr-file {paths['stderr']}\n"
        )

    def bootstrap_session(self, agent_id: str, pane_id: str, cwd: str) -> dict[str, str]:
        paths = self.session_paths(agent_id)
        self.tmux.pipe_pane_to_file(pane_id, paths["stdout"])
        self.tmux.send_input(pane_id, self.build_bootstrap_command(agent_id, cwd))
        return {key: str(value) for key, value in paths.items()}

    def dispatch_task(self, pane_id: str, contract: TaskContract) -> str:
        prompt = self.build_task_prompt(contract)
        self.log.info("codex.dispatch", pane_id=pane_id, task_id=contract.task_id)
        self.tmux.send_input(pane_id, prompt)
        return prompt
