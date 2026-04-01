from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from app.bootstrap import _bootstrap_services_cached, bootstrap_services

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_BIN = REPO_ROOT / ".venv" / "bin" / "alvis"


def create_cli_env(tmp_path: Path) -> dict[str, str]:
    repo_root = tmp_path / "project"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "README.md").write_text("shared root")

    data_dir = tmp_path / "data"
    fake_codex = REPO_ROOT / "tests" / "fixtures" / "fake_codex_session.py"
    env = os.environ.copy()
    env.update(
        {
            "ALVIS_REPO_ROOT": str(repo_root),
            "ALVIS_DATA_DIR": str(data_dir),
            "ALVIS_DB_PATH": str(data_dir / "alvis.db"),
            "ALVIS_LOG_DIR": str(data_dir / "logs"),
            "ALVIS_RUNTIME_DIR": str(data_dir / "runtime"),
            "ALVIS_WORKTREE_ROOT": str(tmp_path / "runtime-cache"),
            "ALVIS_TMUX_PREFIX": f"alvis-e2e-{uuid4().hex[:6]}",
            "ALVIS_CODEX_COMMAND": f"{REPO_ROOT / '.venv' / 'bin' / 'python'} {fake_codex}",
        }
    )
    return env


def bootstrap_cli_services(env: dict[str, str]):
    keys = [
        "ALVIS_REPO_ROOT",
        "ALVIS_DATA_DIR",
        "ALVIS_DB_PATH",
        "ALVIS_LOG_DIR",
        "ALVIS_RUNTIME_DIR",
        "ALVIS_WORKTREE_ROOT",
        "ALVIS_TMUX_PREFIX",
        "ALVIS_CODEX_COMMAND",
    ]
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update({key: env[key] for key in keys})
    _bootstrap_services_cached.cache_clear()
    try:
        return bootstrap_services(env["ALVIS_REPO_ROOT"])
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_cli(env: dict[str, str], *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI_BIN), *args],
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
        env=env,
        cwd=env["ALVIS_REPO_ROOT"],
    )


def test_cli_run_and_status_pretty_output(tmp_path):
    env = create_cli_env(tmp_path)
    services = bootstrap_cli_services(env)
    payload = services.start_or_attach_default_team()
    team_id = payload["team_id"]
    run_result = run_cli(env, "run", team_id, "fix a bug")
    status_result = run_cli(env, "status", team_id)
    status_json = json.loads(run_cli(env, "status", team_id, "--json").stdout)

    assert "실행:" in run_result.stdout
    assert "상태:" in run_result.stdout
    assert "자동 handoff:" in status_result.stdout
    assert "최종 출력 후보:" in status_result.stdout
    assert status_json["latest_run"]["run_id"].startswith("run-")
    assert status_json["pending_reviews"] == []


def test_cli_run_routes_to_reviewer_and_finishes_without_manual_review(tmp_path):
    env = create_cli_env(tmp_path)
    services = bootstrap_cli_services(env)
    payload = services.start_or_attach_default_team()
    team_id = payload["team_id"]
    run_state = json.loads(run_cli(env, "run", team_id, "fix a bug", "--json").stdout)
    status_json = json.loads(run_cli(env, "status", team_id, "--json").stdout)

    assert run_state["status"] == "done"
    assert status_json["latest_run"]["status"] == "done"
    assert status_json["pending_reviews"] == []
    assert status_json["handoffs"]
    assert status_json["final_output_ready"] is True
    assert status_json["final_output_candidate"]["summary"].startswith("synthetic result for")
    task_outputs = [task["latest_output"] for task in status_json["tasks"] if task["latest_output"]]
    assert task_outputs
    assert any(output["summary"].startswith("synthetic result for") for output in task_outputs)


def test_cli_status_json_includes_handoff_and_final_candidate_details(tmp_path):
    env = create_cli_env(tmp_path)
    services = bootstrap_cli_services(env)
    payload = services.start_or_attach_default_team()
    team_id = payload["team_id"]
    run_cli(env, "run", team_id, "fix a bug", "--json")
    status_json = json.loads(run_cli(env, "status", team_id, "--json").stdout)

    assert status_json["handoffs"]
    handoff = status_json["handoffs"][0]
    assert handoff["parent_task_id"]
    assert handoff["target_role_alias"] == "reviewer"
    assert status_json["final_output_candidate"]
    assert status_json["final_output_ready"] is True
    assert "summary" in status_json["final_output_candidate"]


def test_cli_recover_reports_missing_panes_after_killed_session(tmp_path):
    env = create_cli_env(tmp_path)
    services = bootstrap_cli_services(env)
    payload = services.start_or_attach_default_team()
    team_id = payload["team_id"]
    start_payload = {"session_name": payload["session_name"]}
    run_cli(env, "run", team_id, "exercise recovery", "--json")

    try:
        subprocess.run(["tmux", "kill-session", "-t", start_payload["session_name"]], check=True, capture_output=True, text=True)
        recover_pretty = run_cli(env, "recover", "--team-id", team_id)
        recover_json = json.loads(run_cli(env, "recover", "--team-id", team_id, "--json").stdout)

        assert "복구 보고서" in recover_pretty.stdout
        assert recover_json["missing_panes"]
        assert recover_json["actions_taken"]
    finally:
        subprocess.run(["tmux", "kill-session", "-t", start_payload["session_name"]], check=False, capture_output=True, text=True)


def test_cli_start_reuses_existing_team_attach_flow(tmp_path):
    env = create_cli_env(tmp_path)
    services = bootstrap_cli_services(env)
    first = services.start_or_attach_default_team()
    second = services.start_or_attach_default_team()
    assert second["action"] == "attached_existing"
    assert second["team_id"] == first["team_id"]
