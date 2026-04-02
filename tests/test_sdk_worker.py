from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime.sdk_worker import LocalToolBridge, run_sdk_worker
from app.schemas import TaskContract


class FakeResponses:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(
                id="resp-1",
                output=[
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "write_file",
                        "arguments": json.dumps({"path": "README.md", "content": "updated by sdk worker"}),
                    }
                ],
                output_text="",
            )
        return SimpleNamespace(
            id="resp-2",
            output=[],
            output_text=json.dumps(
                {
                    "status_signal": "done",
                    "summary": "Updated the assigned file.",
                    "question_for_leader": [],
                    "requested_context": [],
                    "followup_suggestion": [],
                    "dependency_note": [],
                    "changed_files": ["README.md"],
                    "test_results": ["not run"],
                    "risk_flags": [],
                }
            ),
        )


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_sdk_worker_executes_tool_calls_and_returns_structured_output(tmp_path: Path):
    repo_root = tmp_path / "project"
    repo_root.mkdir()
    readme = repo_root / "README.md"
    readme.write_text("before", encoding="utf-8")
    contract = TaskContract(
        task_id="task-1",
        role="implementer",
        role_alias="executor",
        cwd=str(repo_root),
        goal="Update README.md",
        owned_paths=["README.md"],
    )

    output, final_text = run_sdk_worker(
        prompt_text="Update README.md with a short note.",
        contract=contract,
        agent_id="agent-1",
        api_key="test-key",
        model="gpt-5.4",
        reasoning_effort="medium",
        max_tool_rounds=4,
        timeout_seconds=30,
        client=FakeClient(),
    )

    assert readme.read_text(encoding="utf-8") == "updated by sdk worker"
    assert output.status_signal == "done"
    assert output.changed_files == ["README.md"]
    assert "Updated the assigned file." in final_text


def test_local_tool_bridge_blocks_writes_outside_owned_paths(tmp_path: Path):
    repo_root = tmp_path / "project"
    repo_root.mkdir()
    contract = TaskContract(
        task_id="task-2",
        role="implementer",
        role_alias="executor",
        cwd=str(repo_root),
        goal="Only touch src/app.py",
        owned_paths=["src/app.py"],
    )
    bridge = LocalToolBridge(contract, timeout_seconds=10)

    with pytest.raises(RuntimeError):
        bridge._tool_write_file({"path": "README.md", "content": "nope"})
