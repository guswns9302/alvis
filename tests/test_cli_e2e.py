from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_BIN = REPO_ROOT / ".venv" / "bin" / "alvis"


def create_cli_env(tmp_path: Path) -> dict[str, str]:
    repo_clone = tmp_path / "repo-clone"
    subprocess.run(["git", "clone", "--no-hardlinks", str(REPO_ROOT), str(repo_clone)], check=True, capture_output=True, text=True)

    data_dir = tmp_path / "data"
    env = os.environ.copy()
    env.update(
        {
            "ALVIS_REPO_ROOT": str(repo_clone),
            "ALVIS_DATA_DIR": str(data_dir),
            "ALVIS_DB_PATH": str(data_dir / "alvis.db"),
            "ALVIS_LOG_DIR": str(data_dir / "logs"),
            "ALVIS_RUNTIME_DIR": str(data_dir / "runtime"),
            "ALVIS_WORKTREE_ROOT": str(tmp_path / "worktrees"),
            "ALVIS_TMUX_PREFIX": f"alvis-e2e-{uuid4().hex[:6]}",
            "ALVIS_CODEX_COMMAND": "sh",
        }
    )
    return env


def run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI_BIN), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_cli_run_and_status_pretty_output(tmp_path):
    env = create_cli_env(tmp_path)
    team_id = f"cli-run-{uuid4().hex[:6]}"

    run_cli(env, "team", "create", team_id, "--workers", "2")
    run_result = run_cli(env, "run", team_id, "fix a bug")
    status_result = run_cli(env, "status", team_id)
    status_json = json.loads(run_cli(env, "status", team_id, "--json").stdout)

    assert "Run:" in run_result.stdout
    assert "Status:" in run_result.stdout
    assert "Team:" in status_result.stdout
    assert "Latest run:" in status_result.stdout
    assert status_json["latest_run"]["run_id"].startswith("run-")


def test_cli_review_approve_resumes_same_run(tmp_path):
    env = create_cli_env(tmp_path)
    team_id = f"cli-approve-{uuid4().hex[:6]}"

    run_cli(env, "team", "create", team_id, "--workers", "2")
    run_state = json.loads(run_cli(env, "run", team_id, "fix a bug", "--json").stdout)
    reviews = json.loads(run_cli(env, "review", "list", "--json").stdout)
    review_id = next(review["review_id"] for review in reviews if review["run_id"] == run_state["run_id"])

    approve_result = run_cli(env, "review", "approve", review_id)
    status_json = json.loads(run_cli(env, "status", team_id, "--json").stdout)

    assert f"Review approved: {review_id}" in approve_result.stdout
    assert f"Run: {run_state['run_id']}" in approve_result.stdout
    assert status_json["latest_run"]["status"] == "done"


def test_cli_review_reject_pretty_output_contains_replan(tmp_path):
    env = create_cli_env(tmp_path)
    team_id = f"cli-reject-{uuid4().hex[:6]}"

    run_cli(env, "team", "create", team_id, "--workers", "2")
    run_state = json.loads(run_cli(env, "run", team_id, "fix a bug", "--json").stdout)
    reviews = json.loads(run_cli(env, "review", "list", "--json").stdout)
    review_id = next(review["review_id"] for review in reviews if review["run_id"] == run_state["run_id"])

    reject_result = run_cli(env, "review", "reject", review_id, "--reason", "Need a more specific corrective task")
    logs_json = json.loads(run_cli(env, "logs", team_id, "--json").stdout)

    assert f"Review rejected: {review_id}" in reject_result.stdout
    assert "Replan task:" in reject_result.stdout
    assert any(event["event_type"] == "replan.generated" for event in logs_json)


def test_cli_recover_reports_missing_panes_after_killed_session(tmp_path):
    env = create_cli_env(tmp_path)
    team_id = f"cli-recover-{uuid4().hex[:6]}"

    run_cli(env, "team", "create", team_id, "--workers", "1")
    start_payload = json.loads(run_cli(env, "team", "start", team_id, "--json").stdout)
    run_cli(env, "run", team_id, "exercise recovery", "--json")

    try:
        subprocess.run(["tmux", "kill-session", "-t", start_payload["session_name"]], check=True, capture_output=True, text=True)
        recover_pretty = run_cli(env, "recover", "--team-id", team_id)
        recover_json = json.loads(run_cli(env, "recover", "--team-id", team_id, "--json").stdout)

        assert "Recovery report" in recover_pretty.stdout
        assert recover_json["missing_panes"]
        assert recover_json["actions_taken"]
    finally:
        subprocess.run(["tmux", "kill-session", "-t", start_payload["session_name"]], check=False, capture_output=True, text=True)
