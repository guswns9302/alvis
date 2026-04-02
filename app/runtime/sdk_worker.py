from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.schemas import AgentOutput, TaskContract


def create_openai_client(api_key: str | None = None):
    if importlib.util.find_spec("openai") is None:
        raise RuntimeError("OpenAI Python SDK is not installed.")
    if not (api_key or os.getenv("OPENAI_API_KEY")):
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    from openai import OpenAI

    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    return OpenAI(**kwargs)


class LocalToolBridge:
    def __init__(self, contract: TaskContract, *, timeout_seconds: int):
        self.contract = contract
        self.cwd = Path(contract.cwd).resolve()
        self.timeout_seconds = timeout_seconds
        self.owned_paths = [self._resolve_owned_path(path) for path in contract.owned_paths]

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "list_files",
                "description": "List files under a path relative to the task cwd.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "read_file",
                "description": "Read a text file relative to the task cwd.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "write_file",
                "description": "Write a text file under the task owned paths.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "search_text",
                "description": "Search for text under a path relative to the task cwd.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "run_command",
                "description": "Run a non-destructive shell command from the task cwd.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return json.dumps({"ok": False, "error": f"unknown tool: {name}"}, ensure_ascii=False)
        try:
            result = handler(arguments)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            result = {"ok": False, "error": str(exc)}
        return json.dumps(result, ensure_ascii=False)

    def _resolve_owned_path(self, path: str) -> Path:
        return self._resolve_path(path, for_write=False)

    def _resolve_path(self, raw_path: str | None, *, for_write: bool) -> Path:
        candidate = (self.cwd / (raw_path or ".")).resolve()
        if not str(candidate).startswith(str(self.cwd)):
            raise RuntimeError(f"path escapes cwd: {raw_path}")
        if for_write:
            if not self.owned_paths:
                raise RuntimeError("no writable paths assigned to this task")
            if not any(candidate == owned or str(candidate).startswith(f"{owned}{os.sep}") for owned in self.owned_paths):
                raise RuntimeError(f"path is outside owned_paths: {raw_path}")
        return candidate

    def _tool_list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root = self._resolve_path(arguments.get("path"), for_write=False)
        files: list[str] = []
        for path in sorted(root.rglob("*")):
            if len(files) >= 200:
                break
            if path.is_file():
                files.append(str(path.relative_to(self.cwd)))
        return {"ok": True, "files": files}

    def _tool_read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(arguments["path"], for_write=False)
        return {"ok": True, "path": str(path.relative_to(self.cwd)), "content": path.read_text(encoding="utf-8")}

    def _tool_write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(arguments["path"], for_write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(arguments["content"]), encoding="utf-8")
        return {"ok": True, "path": str(path.relative_to(self.cwd))}

    def _tool_search_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments["query"])
        root = self._resolve_path(arguments.get("path"), for_write=False)
        try:
            result = subprocess.run(
                ["rg", "-n", "--no-heading", "--color", "never", query, str(root)],
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                timeout=min(self.timeout_seconds, 30),
                check=False,
            )
            matches = [line for line in result.stdout.splitlines() if line.strip()][:100]
            return {"ok": True, "matches": matches}
        except FileNotFoundError:
            matches: list[str] = []
            for path in sorted(root.rglob("*")):
                if len(matches) >= 100 or not path.is_file():
                    continue
                try:
                    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                        if query in line:
                            matches.append(f"{path.relative_to(self.cwd)}:{lineno}:{line.strip()}")
                            if len(matches) >= 100:
                                break
                except OSError:
                    continue
            return {"ok": True, "matches": matches}

    def _tool_run_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = str(arguments["command"]).strip()
        blocked = ("rm -rf", "git reset", "git checkout --", "shutdown", "reboot")
        if any(token in command for token in blocked):
            raise RuntimeError("command is not allowed")
        completed = subprocess.run(
            ["/bin/zsh", "-lc", command],
            cwd=str(self.cwd),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
        }


def _response_field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _extract_function_calls(response: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in _response_field(response, "output", []) or []:
        if _response_field(item, "type") != "function_call":
            continue
        calls.append(
            {
                "call_id": _response_field(item, "call_id") or _response_field(item, "id"),
                "name": _response_field(item, "name"),
                "arguments": _response_field(item, "arguments"),
            }
        )
    return calls


def _structured_response_instructions() -> str:
    return (
        "Return the final answer as a single JSON object. "
        "The JSON object must contain exactly these keys: "
        "status_signal, summary, question_for_leader, requested_context, "
        "followup_suggestion, dependency_note, changed_files, test_results, risk_flags. "
        "Use arrays for all list fields. Do not wrap the JSON in markdown fences."
    )


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not raw_arguments:
        return {}
    return json.loads(str(raw_arguments))


def _normalize_response_text(response: Any) -> str:
    text = _response_field(response, "output_text", "") or ""
    if text:
        return str(text).strip()
    for item in _response_field(response, "output", []) or []:
        if _response_field(item, "type") == "message":
            content = _response_field(item, "content", []) or []
            fragments = []
            for chunk in content:
                if _response_field(chunk, "type") in {"output_text", "text"}:
                    fragments.append(str(_response_field(chunk, "text", "")))
            if fragments:
                return "".join(fragments).strip()
    return ""


def _normalize_agent_output(payload_text: str, *, task_id: str, agent_id: str) -> AgentOutput:
    normalized = payload_text.strip()
    if normalized.startswith("```"):
        normalized = normalized.strip("`")
        if normalized.startswith("json"):
            normalized = normalized[4:].strip()
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise RuntimeError("SDK worker returned a non-object response.")
    payload.setdefault("task_id", task_id)
    payload.setdefault("agent_id", agent_id)
    payload.setdefault("kind", "final")
    return AgentOutput.model_validate(payload)


def run_sdk_worker(
    *,
    prompt_text: str,
    contract: TaskContract,
    agent_id: str,
    api_key: str | None,
    model: str,
    reasoning_effort: str,
    max_tool_rounds: int,
    timeout_seconds: int,
    client: Any = None,
) -> tuple[AgentOutput, str]:
    if client is None:
        client = create_openai_client(api_key)
    bridge = LocalToolBridge(contract, timeout_seconds=timeout_seconds)
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": _structured_response_instructions()},
            {"role": "user", "content": prompt_text},
        ],
        tools=bridge.tool_specs(),
        reasoning={"effort": reasoning_effort},
    )
    rounds = 0
    while True:
        calls = _extract_function_calls(response)
        if not calls:
            break
        rounds += 1
        if rounds > max_tool_rounds:
            raise RuntimeError("SDK worker exceeded the maximum tool-call rounds.")
        tool_outputs = []
        for call in calls:
            result = bridge.execute(call["name"], _parse_arguments(call["arguments"]))
            tool_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": result,
                }
            )
        response = client.responses.create(
            model=model,
            previous_response_id=_response_field(response, "id"),
            input=tool_outputs,
            tools=bridge.tool_specs(),
            reasoning={"effort": reasoning_effort},
        )
    final_text = _normalize_response_text(response)
    if not final_text:
        raise RuntimeError("SDK worker did not return a final response.")
    return _normalize_agent_output(final_text, task_id=contract.task_id, agent_id=agent_id), final_text
