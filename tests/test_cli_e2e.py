from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4


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


def run_cli(env: dict[str, str], *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI_BIN), *args],
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_cli_run_and_status_pretty_output(tmp_path):
    env = create_cli_env(tmp_path)
    team_id = f"cli-run-{uuid4().hex[:6]}"

    run_cli(env, "team", "create", team_id, "--worker-1-role", "implementer:builder", "--worker-2-role", "reviewer:checker", "--no-attach")
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
    team_id = f"cli-auto-{uuid4().hex[:6]}"

    run_cli(env, "team", "create", team_id, "--worker-1-role", "implementer:builder", "--worker-2-role", "reviewer:checker", "--no-attach")
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
    team_id = f"cli-handoff-details-{uuid4().hex[:6]}"

    run_cli(env, "team", "create", team_id, "--worker-1-role", "implementer:builder", "--worker-2-role", "reviewer:checker", "--no-attach")
    run_cli(env, "run", team_id, "fix a bug", "--json")
    status_json = json.loads(run_cli(env, "status", team_id, "--json").stdout)

    assert status_json["handoffs"]
    handoff = status_json["handoffs"][0]
    assert handoff["parent_task_id"]
    assert handoff["target_role_alias"] == "checker"
    assert status_json["final_output_candidate"]
    assert status_json["final_output_ready"] is True
    assert "summary" in status_json["final_output_candidate"]


def test_cli_recover_reports_missing_panes_after_killed_session(tmp_path):
    env = create_cli_env(tmp_path)
    team_id = f"cli-recover-{uuid4().hex[:6]}"

    start_payload = json.loads(
        run_cli(
            env,
            "team",
            "create",
            team_id,
            "--worker-1-role",
            "implementer:builder",
            "--worker-2-role",
            "reviewer:checker",
            "--json",
            "--no-attach",
        ).stdout
    )["start_result"]
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


def test_cli_team_create_interactive_wizard(tmp_path):
    env = create_cli_env(tmp_path)
    team_id = f"cli-wizard-{uuid4().hex[:6]}"

    result = run_cli(
        env,
        "team",
        "create",
        "--json",
        "--no-attach",
        input_text=f"{team_id}\n1\nbackend\n2\ntest\n",
    )
    payload = json.loads(result.stdout)

    assert payload["team_id"] == team_id
    assert payload["workers"][0]["role"] == "implementer"
    assert payload["workers"][0]["role_alias"] == "backend"
    assert payload["workers"][1]["role"] == "reviewer"
    assert payload["workers"][1]["role_alias"] == "test"
    assert payload["start_result"]["session_name"].startswith("alvis-e2e-")
