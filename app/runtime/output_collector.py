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
    RESULT_START = "ALVIS_RESULT_START"
    RESULT_END = "ALVIS_RESULT_END"
    SECTION_HEADERS = ("SUMMARY", "CHANGED_FILES", "TEST_RESULTS", "RISK_FLAGS")
    NOISE_PATTERNS = (
        re.compile(r"^\]7;file://.*$"),
        re.compile(r"^(?:\?\d+h|\?\d+l)$"),
        re.compile(r"^\x1b\[\?2004[hl]$"),
    )

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
        clean_text = self.ANSI_PATTERN.sub("", log_text).replace("\r", "\n")
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
            "summary": "",
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
            if line == "CHANGED_FILES:":
                current_section = "changed_files"
                continue
            if line == "TEST_RESULTS:":
                current_section = "test_results"
                continue
            if line == "RISK_FLAGS:":
                current_section = "risk_flags"
                continue
            if current_section in {"changed_files", "test_results", "risk_flags"}:
                normalized = re.sub(r"^[-*]\s*", "", line)
                if normalized:
                    sections[current_section].append(normalized)  # type: ignore[index]
            elif current_section == "summary" and not sections["summary"]:
                sections["summary"] = line
        return sections

    def _heuristic_output(self, *, agent_id: str, task_id: str, clean_text: str) -> AgentOutput:
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

    def summarize_task_output(self, *, agent_id: str, task_id: str, log_text: str) -> AgentOutput:
        clean_text = self._normalize_text(log_text)
        heuristic = self._heuristic_output(agent_id=agent_id, task_id=task_id, clean_text=clean_text)
        block, is_complete = self._extract_latest_result_block(clean_text)
        if not block:
            return heuristic

        structured = self._parse_structured_block(block)
        summary = structured["summary"] or heuristic.summary
        changed_files = list(structured["changed_files"]) or heuristic.changed_files
        test_results = list(structured["test_results"]) or heuristic.test_results
        risk_flags = list(structured["risk_flags"]) or heuristic.risk_flags
        kind = "final" if is_complete or any((summary, changed_files, test_results, risk_flags)) else heuristic.kind
        return AgentOutput(
            task_id=task_id,
            agent_id=agent_id,
            kind=kind,
            summary=summary,
            changed_files=changed_files,
            test_results=test_results,
            risk_flags=risk_flags,
        )
