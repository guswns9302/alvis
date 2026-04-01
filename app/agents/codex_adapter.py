from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas import TaskContract


class CodexAdapter:
    STDERR_PATTERNS = (
        (
            re.compile(r"npm error code EACCES", re.IGNORECASE),
            "Codex가 전역 npm 업데이트를 시도했지만 권한 오류(EACCES)로 종료되었습니다.",
        ),
        (
            re.compile(r"npm install -g @openai/codex", re.IGNORECASE),
            "Codex 업데이트 프롬프트가 전역 설치를 시도하다 실패했습니다.",
        ),
        (
            re.compile(r"permission denied", re.IGNORECASE),
            "Codex 실행 중 권한 오류가 발생했습니다.",
        ),
        (
            re.compile(r"Update available!", re.IGNORECASE),
            "Codex 업데이트 프롬프트가 표시된 것으로 보입니다.",
        ),
    )

    def __init__(
        self,
        codex_command: str,
        runtime_dir: Path,
    ):
        self.codex_command = codex_command
        self.runtime_dir = runtime_dir

    def build_task_prompt(self, contract: TaskContract) -> str:
        constraints = "\n".join(f"- {item}" for item in contract.constraints) or "- None"
        expected = "\n".join(f"- {item}" for item in contract.expected_output) or "- Summary"
        context_lines = "\n".join(f"- {key}: {value}" for key, value in contract.context.items()) or "- None"
        owned_paths = "\n".join(f"- {item}" for item in contract.owned_paths) or "- No writable paths assigned"
        coordination_context = "\n".join(
            f"- {item.get('kind', 'context')}: {item.get('summary') or item.get('message') or item.get('detail') or item}"
            for item in contract.coordination_context
        ) or "- None"
        return (
            "[ALVIS TASK]\n"
            f"task_id: {contract.task_id}\n"
            f"task_type: {contract.task_type}\n"
            f"role: {contract.role}\n"
            f"role_alias: {contract.role_alias or contract.role}\n"
            f"cwd: {contract.cwd}\n"
            f"goal: {contract.goal}\n"
            "owned_paths:\n"
            f"{owned_paths}\n"
            "coordination_context:\n"
            f"{coordination_context}\n"
            "constraints:\n"
            f"{constraints}\n"
            "expected_output:\n"
            f"{expected}\n"
            f"completion_rule: {contract.completion_rule}\n"
            "response_rules:\n"
            "- If the runner provides an output schema, return a final response that conforms to that schema.\n"
            "- STATUS must be one of: done, need_input, blocked, needs_review.\n"
            "- If the task is off-target or incomplete, use STATUS: blocked or STATUS: needs_review and explain why.\n"
            "context:\n"
            f"{context_lines}\n"
        )

    def session_paths(self, agent_id: str) -> dict[str, Path]:
        agent_dir = self.runtime_dir / "agents" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return {
            "dir": agent_dir,
            "heartbeat": agent_dir / "heartbeat.json",
            "state": agent_dir / "session_state.json",
            "stdout": agent_dir / "pane.log",
            "stderr": agent_dir / "stderr.log",
            "inbox": agent_dir / "prompt_inbox.jsonl",
            "prompt": agent_dir / "task_prompt.txt",
            "last_message": agent_dir / "last_message.txt",
            "schema_output": agent_dir / "task_output.json",
        }

    def reset_session_files(self, agent_id: str) -> dict[str, Path]:
        paths = self.session_paths(agent_id)
        for key in ("heartbeat", "state", "stdout", "stderr", "inbox", "prompt", "last_message", "schema_output"):
            paths[key].write_text("")
        return paths

    def read_session_state(self, agent_id: str) -> dict:
        path = self.session_paths(agent_id)["state"]
        if not path.exists():
            return {"status": "not_ready"}
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"status": "not_ready", "reason": "invalid_state_file"}

    def runtime_health(self, agent_id: str, pane_exists: bool) -> dict:
        state = self.read_session_state(agent_id)
        status = state.get("status", "not_ready")
        stderr_summary = self.stderr_summary(agent_id)
        if not pane_exists and status in {"not_ready", "starting"}:
            status = "missing_pane"
        return {
            "status": status,
            "ready": status in {"ready", "running", "exited"},
            "pid": state.get("pid"),
            "exit_code": state.get("exit_code"),
            "reason": state.get("reason"),
            "error_summary": stderr_summary.get("summary"),
            "error_hint": stderr_summary.get("hint"),
            "last_stderr_line": stderr_summary.get("last_line"),
        }

    def stderr_summary(self, agent_id: str) -> dict:
        stderr_path = self.session_paths(agent_id)["stderr"]
        if not stderr_path.exists():
            return {}
        text = stderr_path.read_text(errors="ignore").strip()
        if not text:
            return {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        last_line = lines[-1] if lines else ""
        for pattern, summary in self.STDERR_PATTERNS:
            if pattern.search(text):
                return {
                    "summary": summary,
                    "hint": "터미널에서 `codex`를 직접 실행해 업데이트 프롬프트를 넘기거나 권한 문제를 해결한 뒤 다시 시도하세요.",
                    "last_line": last_line,
                }
        return {
            "summary": "Codex 세션이 stderr를 남기고 종료되었습니다.",
            "hint": "agent stderr 로그를 확인하세요.",
            "last_line": last_line,
        }
