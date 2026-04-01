from app.cli_formatters import format_clean, format_logs, format_recover, format_start, format_status


def test_format_status_renders_core_sections():
    text = format_status(
        {
            "team_id": "demo",
            "session_name": "alvis-demo",
            "agents": [
                {
                    "agent_id": "demo-worker-1",
                    "role": "implementer",
                    "role_alias": "builder",
                    "status": "running",
                    "pane": "%1",
                    "task": "task-1",
                    "runtime_health": {
                        "status": "exited",
                        "ready": False,
                        "error_summary": "Codex가 전역 npm 업데이트를 시도했지만 권한 오류(EACCES)로 종료되었습니다.",
                        "error_hint": "터미널에서 `codex`를 직접 실행해 업데이트 프롬프트를 넘기거나 권한 문제를 해결한 뒤 다시 시도하세요.",
                    },
                    "pid": 123,
                    "exit_code": 1,
                }
            ],
            "execution_summary": {
                "dispatching_tasks": 1,
                "waiting_interactions": 0,
                "blocked_tasks": 0,
                "run_age_seconds": 1.2,
                "latest_task_update_age_seconds": 0.5,
                "oldest_pending_interaction_age_seconds": None,
                "last_important_event": "Task assigned",
            },
            "latest_run": {
                "run_id": "run-1",
                "status": "running",
                "request": "fix bug",
                "final_response": None,
                "checkpoint": {"thread_id": "run-1", "next_node": "wait_for_updates"},
            },
            "tasks": [{"task_id": "task-1", "title": "Implement", "goal": "fix bug", "target_role_alias": "builder", "owned_paths": ["src/app.py"], "status": "running", "agent_id": "demo-worker-1", "result_summary": None}],
            "pending_reviews": [],
            "handoffs": [
                {
                    "task_id": "task-2",
                    "parent_task_id": "task-1",
                    "agent_id": "demo-worker-2",
                    "title": "Validate and summarize",
                    "status": "done",
                    "target_role_alias": "checker",
                }
            ],
            "final_output_candidate": {
                "task_id": "task-2",
                "agent_id": "demo-worker-2",
                "summary": "validated output",
            },
            "final_output_ready": True,
            "redo_tasks": [],
            "runtime_issues": {
                "missing_runtime_state": [],
                "stale_heartbeat": [],
                "runtime_not_ready": [],
                "exited_runners": [],
                "uncollected_outputs": [],
                "orphaned_tasks": [],
                "dangling_runs": [],
            },
        }
    )
    assert "팀: demo" in text
    assert "최근 실행: run-1" in text
    assert "체크포인트: next=wait_for_updates thread=run-1" in text
    assert "실행 요약:" in text
    assert "run_age=1.2s" in text
    assert "에이전트:" in text
    assert "자동 handoff:" in text
    assert "validated output" in text
    assert "ready=yes" in text
    assert "권한 오류(EACCES)" in text


def test_format_logs_prefers_event_summary():
    text = format_logs(
        [
            {
                "event_id": 1,
                "event_type": "task.assigned",
                "agent_id": "demo-worker-1",
                "task_id": "task-1",
                "payload": {"summary": "Task assigned"},
            }
        ]
    )
    assert "task.assigned" in text
    assert "Task assigned" in text


def test_format_cleanup_renders_counts():
    text = format_clean(
        {
            "removed_count": 1,
            "skipped_count": 1,
            "removed_teams": [{"team_id": "demo-a", "session_name": "alvis-demo-a"}],
            "skipped_teams": [{"team_id": "demo-b", "session_name": "alvis-demo-b"}],
        }
    )
    assert "removed_teams: 1" in text
    assert "skipped_teams: 1" in text


def test_format_status_renders_handoff_details():
    text = format_status(
        {
            "team_id": "demo",
            "session_name": "alvis-demo",
            "agents": [],
            "latest_run": None,
            "tasks": [],
            "handoffs": [
                {
                    "task_id": "task-2",
                    "parent_task_id": "task-1",
                    "agent_id": "demo-worker-2",
                    "status": "done",
                    "target_role_alias": "checker",
                }
            ],
            "final_output_candidate": None,
            "final_output_ready": False,
            "redo_tasks": [
                {
                    "task_id": "task-3",
                    "parent_task_id": "task-2",
                    "agent_id": "demo-worker-1",
                    "status": "running",
                    "target_role_alias": "builder",
                }
            ],
            "runtime_issues": {
                "missing_runtime_state": [],
                "stale_heartbeat": [],
                "runtime_not_ready": [],
                "exited_runners": [],
                "uncollected_outputs": [],
                "orphaned_tasks": [],
                "dangling_runs": [],
            },
        }
    )
    assert "parent=task-1" in text
    assert "role=checker" in text
    assert "재작업 작업:" in text


def test_format_start_renders_existing_session_attach():
    text = format_start(
        {
            "action": "attached_existing",
            "team_id": "demo",
        }
    )

    assert "기존 팀 진입: demo" in text


def test_format_recover_renders_session_errors():
    text = format_recover(
        {
            "missing_runtime_state": [],
            "stale_heartbeat": [],
            "runtime_not_ready": [],
            "exited_runners": ["demo-worker-1"],
            "uncollected_outputs": [],
            "orphaned_tasks": [],
            "orphaned_reviews": [],
            "dangling_runs": [],
            "reconciled_runs": [],
            "scope_conflicts": [],
            "cleanup_candidates": [],
            "actions_taken": [],
            "session_errors": [
                {
                    "agent_id": "demo-worker-1",
                    "error_summary": "Codex가 전역 npm 업데이트를 시도했지만 권한 오류(EACCES)로 종료되었습니다.",
                    "error_hint": "터미널에서 `codex`를 직접 실행해 업데이트 프롬프트를 넘기거나 권한 문제를 해결한 뒤 다시 시도하세요.",
                }
            ],
        }
    )
    assert "감지된 세션 오류:" in text
    assert "exited_runners: 1" in text
    assert "demo-worker-1" in text
