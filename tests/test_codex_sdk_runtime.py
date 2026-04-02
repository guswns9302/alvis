from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import Settings
from app.runtime.codex_sdk_runtime import (
    ensure_node_runtime_assets,
    normalize_command_backend,
    run_codex_sdk_worker,
    verify_codex_sdk_runtime,
)


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        app_home=tmp_path / ".alvis",
        repo_root=tmp_path,
        data_dir=data_dir,
        db_path=data_dir / "alvis.db",
        log_dir=data_dir / "logs",
        runtime_dir=data_dir / "runtime",
        worktree_root=data_dir / "worktrees",
        worker_backend="codex-sdk",
        worker_model="gpt-5.4",
        codex_api_key="test-key",
        codex_command="/usr/local/bin/codex",
    )


def test_ensure_node_runtime_assets_writes_worker_files(tmp_path: Path):
    settings = _settings(tmp_path)

    assets = ensure_node_runtime_assets(settings)

    assert assets["package"].exists()
    assert assets["worker"].exists()
    assert "@openai/codex-sdk" in assets["package"].read_text(encoding="utf-8")


def test_verify_codex_sdk_runtime_reports_installed(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    ensure_node_runtime_assets(settings)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "/tmp/fake\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = verify_codex_sdk_runtime(settings)

    assert result["node_available"] is True
    assert result["npm_available"] is True
    assert result["sdk_installed"] is True
    assert calls[0] == ["node", "--version"]
    assert calls[1] == ["npm", "--version"]
    assert calls[2][:3] == ["node", "--input-type=module", "-e"]


def test_run_codex_sdk_worker_sets_codex_api_key(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("hello", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"task_id":"task-1","role":"implementer","cwd":"%s","goal":"hello"}' % tmp_path, encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    schema_output = tmp_path / "schema-output.json"
    last_message = tmp_path / "last.txt"
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_codex_sdk_worker(
        settings=settings,
        prompt_file=prompt,
        contract_file=contract,
        schema_file=schema,
        schema_output_file=schema_output,
        last_message_file=last_message,
        agent_id="agent-1",
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert captured["command"][0] == "node"
    assert captured["env"]["CODEX_API_KEY"] == "test-key"


def test_normalize_command_backend_still_normalizes_bare_codex():
    invocation = normalize_command_backend("codex")

    assert invocation[:4] == ["codex", "exec", "--color", "never"]
