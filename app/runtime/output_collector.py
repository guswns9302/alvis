from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.schemas import AgentOutput


@dataclass
class OutputSnapshot:
    heartbeat_at: float | None
    log_text: str


class OutputCollector:
    VALID_STATUS_SIGNALS = {"done", "need_input", "blocked", "needs_review"}
    PARSE_OK = "ok"
    PARSE_NO_RESULT_BLOCK = "no_result_block"
    PARSE_INVALID_RESULT_BLOCK = "invalid_result_block"
    ANSI_PATTERN = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\))")
    RESULT_START = "ALVIS_RESULT_START"
    RESULT_END = "ALVIS_RESULT_END"
    SECTION_HEADERS = (
        "STATUS",
        "SUMMARY",
        "QUESTION_FOR_LEADER",
        "REQUESTED_CONTEXT",
        "FOLLOWUP_SUGGESTION",
        "DEPENDENCY_NOTE",
        "CHANGED_FILES",
        "TEST_RESULTS",
        "RISK_FLAGS",
    )
    NOISE_PATTERNS = (
        re.compile(r"^\]7;file://.*$"),
        re.compile(r"^(?:\?\d+h|\?\d+l)$"),
        re.compile(r"^\x1b\[\?2004[hl]$"),
        re.compile(r"^\[ALVIS SESSION (?:START|EXIT)\]$"),
        re.compile(r'^{"cmd": .*"event": "(?:tmux\.command|codex\.dispatch)".*}$'),
        re.compile(r'^\{"[^"]+":.*"event":\s*"tmux\.command".*\}$'),
        re.compile(r"^(?:zsh|bash|sh): .*$"),
        re.compile(r"^Tip: .*$"),
        re.compile(r"^OpenAI Codex.*$"),
        re.compile(r"^https://chatgpt\.com/codex.*$"),
        re.compile(r"^Run 'codex app' or visit.*$"),
        re.compile(r"^(?:model|directory):\s+.*$"),
        re.compile(r"^gpt-[^ ]+ .*~/work/git/.*$"),
        re.compile(r"^> .*"),
        re.compile(r"^ALVIS_RESULT_(?:START|END)$"),
        re.compile(r"^(?:STATUS|SUMMARY):.*$"),
        re.compile(r"^(?:QUESTION_FOR_LEADER|REQUESTED_CONTEXT|FOLLOWUP_SUGGESTION|DEPENDENCY_NOTE|CHANGED_FILES|TEST_RESULTS|RISK_FLAGS):$"),
        re.compile(r"^(?:task_id|role|cwd|goal|constraints|expected_output|completion_rule|result_template|context):.*$"),
        re.compile(r"^role_alias:.*$"),
        re.compile(r"^task_type:.*$"),
        re.compile(r"^owned_paths:$"),
        re.compile(r"^coordination_context:$"),
        re.compile(r"^\[ALVIS TASK\]$"),
        re.compile(r"^- (?:team_id|run_id|source_review_id|parent_task_id|rejection_reason): .*$"),
        re.compile(r"^[│].*[│]$"),
        re.compile(r"^[╭╰].*[╮╯]$"),
        re.compile(r"^[╭╮╰╯│─]+$"),
    )
    SHELL_NOISE_PATTERN = re.compile(r"^(?:zsh|bash|sh): .*$")

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

    def _normalize_text(self, log_text: str) -> str:
        clean_text = self._strip_ansi(log_text)
        lines = []
        previous = None
        for raw_line in clean_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(pattern.match(line) for pattern in self.NOISE_PATTERNS):
                continue
            if line == previous:
                continue
            previous = line
            lines.append(line)
        return "\n".join(lines)

    def _strip_ansi(self, log_text: str) -> str:
        return self.ANSI_PATTERN.sub("", log_text).replace("\r", "\n")

    def _is_useful_line(self, line: str) -> bool:
        if any(pattern.match(line) for pattern in self.NOISE_PATTERNS):
            return False
        if "[ALVIS TASK]" in line or "ALVIS_RESULT_" in line:
            return False
        if line.startswith("Tip: ") or "tmux.command" in line:
            return False
        return True

    def _extract_latest_result_block(self, clean_text: str) -> tuple[str | None, bool]:
        start = clean_text.rfind(self.RESULT_START)
        if start == -1:
            return None, False
        end = clean_text.find(self.RESULT_END, start)
        if end == -1:
            return clean_text[start + len(self.RESULT_START) :], False
        return clean_text[start + len(self.RESULT_START) : end], True

    def _parse_structured_block(self, block: str) -> dict[str, list[str] | str]:
        sections: dict[str, list[str] | str] = {
            "status_signal": "",
            "summary": "",
            "question_for_leader": [],
            "requested_context": [],
            "followup_suggestion": [],
            "dependency_note": [],
            "changed_files": [],
            "test_results": [],
            "risk_flags": [],
        }
        current_section: str | None = None
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("SUMMARY:"):
                sections["summary"] = line.split(":", 1)[1].strip()
                current_section = "summary"
                continue
            if line.startswith("STATUS:"):
                sections["status_signal"] = line.split(":", 1)[1].strip().lower()
                current_section = "status_signal"
                continue
            if line == "QUESTION_FOR_LEADER:":
                current_section = "question_for_leader"
                continue
            if line == "REQUESTED_CONTEXT:":
                current_section = "requested_context"
                continue
            if line == "FOLLOWUP_SUGGESTION:":
                current_section = "followup_suggestion"
                continue
            if line == "DEPENDENCY_NOTE:":
                current_section = "dependency_note"
                continue
            if line == "CHANGED_FILES:":
                current_section = "changed_files"
                continue
            if line == "TEST_RESULTS:":
                current_section = "test_results"
                continue
            if line == "RISK_FLAGS:":
                current_section = "risk_flags"
                continue
            if current_section in {
                "question_for_leader",
                "requested_context",
                "followup_suggestion",
                "dependency_note",
                "changed_files",
                "test_results",
                "risk_flags",
            }:
                normalized = re.sub(r"^[-*]\s*", "", line)
                if normalized:
                    sections[current_section].append(normalized)  # type: ignore[index]
            elif current_section == "summary" and not sections["summary"]:
                sections["summary"] = line
        return sections

    def _contains_placeholder(self, value: str) -> bool:
        markers = (
            "<done|need_input|blocked|needs_review>",
            "<one concise summary line>",
            "<question that requires leader guidance>",
            "<missing context or dependency>",
            "<suggested next instruction>",
            "<cross-agent dependency note>",
            "<path or file summary>",
            "<test result>",
            "<risk or blocker>",
        )
        normalized = " ".join(value.split()).lower()
        return any(marker.lower() in normalized for marker in markers)

    def _structured_block_is_valid(self, structured: dict[str, Any]) -> bool:
        status_signal = str(structured.get("status_signal", "") or "").strip().lower()
        if status_signal and status_signal not in self.VALID_STATUS_SIGNALS:
            return False
        scalar_values = [
            structured.get("summary", ""),
        ]
        list_values = [
            *structured.get("question_for_leader", []),
            *structured.get("requested_context", []),
            *structured.get("followup_suggestion", []),
            *structured.get("dependency_note", []),
            *structured.get("changed_files", []),
            *structured.get("test_results", []),
            *structured.get("risk_flags", []),
        ]
        return not any(self._contains_placeholder(str(value)) for value in [*scalar_values, *list_values] if value)

    def _heuristic_output(self, *, agent_id: str, task_id: str, clean_text: str) -> AgentOutput:
        lines = [line.strip() for line in clean_text.splitlines() if line.strip() and self._is_useful_line(line.strip())]
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
        summary = relevant[-1] if relevant else "No usable task output captured yet."
        kind = "final" if relevant and (changed_files or test_results or risk_flags) else "delta"
        return AgentOutput(
            task_id=task_id,
            agent_id=agent_id,
            kind=kind,
            summary=summary,
            changed_files=changed_files,
            test_results=test_results,
            risk_flags=risk_flags,
        )

    def summarize_task_output(self, *, agent_id: str, task_id: str, log_text: str, final_message_text: str | None = None) -> AgentOutput:
        parse_input = final_message_text if final_message_text and final_message_text.strip() else log_text
        ansi_stripped = self._strip_ansi(parse_input)
        clean_text = self._normalize_text(log_text)
        heuristic = self._heuristic_output(agent_id=agent_id, task_id=task_id, clean_text=clean_text)
        block, is_complete = self._extract_latest_result_block(ansi_stripped)
        if not block:
            return AgentOutput(
                task_id=task_id,
                agent_id=agent_id,
                kind="delta",
                output_parse_status=self.PARSE_NO_RESULT_BLOCK,
                summary=(
                    heuristic.summary
                    if heuristic.summary != "No usable task output captured yet."
                    else "No usable task output captured yet."
                ),
                changed_files=heuristic.changed_files,
                test_results=heuristic.test_results,
                risk_flags=heuristic.risk_flags,
            )

        structured = self._parse_structured_block(self._strip_ansi(block))
        if not self._structured_block_is_valid(structured):
            return AgentOutput(
                task_id=task_id,
                agent_id=agent_id,
                kind="delta",
                output_parse_status=self.PARSE_INVALID_RESULT_BLOCK,
                summary="No usable task output captured yet.",
            )
        summary = structured["summary"] or heuristic.summary
        changed_files = list(structured["changed_files"]) or heuristic.changed_files
        test_results = list(structured["test_results"]) or heuristic.test_results
        risk_flags = list(structured["risk_flags"]) or heuristic.risk_flags
        kind = "final" if is_complete else "delta"
        return AgentOutput(
            task_id=task_id,
            agent_id=agent_id,
            kind=kind,
            summary=summary,
            output_parse_status=self.PARSE_OK,
            status_signal=(structured["status_signal"] or None),  # type: ignore[arg-type]
            question_for_leader=list(structured["question_for_leader"]),
            requested_context=list(structured["requested_context"]),
            followup_suggestion=list(structured["followup_suggestion"]),
            dependency_note=list(structured["dependency_note"]),
            changed_files=changed_files,
            test_results=test_results,
            risk_flags=risk_flags,
        )
