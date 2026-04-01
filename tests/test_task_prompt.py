from app.schemas import TaskContract
from app.agents.codex_adapter import CodexAdapter
from app.sessions.tmux_manager import TmuxManager
from pathlib import Path


def test_task_prompt_contains_contract_fields():
    adapter = CodexAdapter(TmuxManager("alvis"), "codex", Path("/tmp"), Path("/tmp"), Path("/tmp"))
    prompt = adapter.build_task_prompt(
        TaskContract(
            task_id="task-1",
            role="implementer",
            cwd="/repo",
            goal="Do the work",
            constraints=["No push"],
            expected_output=["Summary"],
        )
    )
    assert "task_id: task-1" in prompt
    assert "goal: Do the work" in prompt
    assert "output schema" in prompt
    assert "ALVIS_RESULT_START" not in prompt
