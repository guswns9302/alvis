from app.cli_formatters import format_cleanup, format_logs, format_status


def test_format_status_renders_core_sections():
    text = format_status(
        {
            "team_id": "demo",
            "session_name": "alvis-demo",
            "agents": [{"agent_id": "demo-worker-1", "role": "implementer", "status": "running", "pane": "%1", "task": "task-1"}],
            "latest_run": {
                "run_id": "run-1",
                "status": "running",
                "request": "fix bug",
                "final_response": None,
                "checkpoint": {"thread_id": "run-1", "next_node": "wait_for_updates"},
            },
            "tasks": [{"task_id": "task-1", "title": "Implement", "status": "running", "agent_id": "demo-worker-1", "result_summary": None}],
            "pending_reviews": [],
            "runtime_issues": {"missing_panes": [], "stale_heartbeat": [], "orphaned_tasks": [], "dangling_runs": []},
        }
    )
    assert "Team: demo" in text
    assert "Latest run: run-1" in text
    assert "Checkpoint: next=wait_for_updates thread=run-1" in text
    assert "Agents:" in text


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
    text = format_cleanup(
        {
            "deleted_worktrees": [{"agent_id": "demo-worker-1"}],
            "skipped_dirty_worktrees": [],
            "skipped_active_worktrees": [{"agent_id": "demo-worker-2"}],
        }
    )
    assert "deleted_worktrees: 1" in text
    assert "skipped_active_worktrees: 1" in text
