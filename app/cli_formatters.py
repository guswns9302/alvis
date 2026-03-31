from __future__ import annotations


def _section(title: str, lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "  - none"
    return f"{title}\n{body}"


def format_team_start(result: dict) -> str:
    panes = result.get("panes", [])
    return "\n".join(
        [
            f"Team started: {result['team_id']}",
            f"Session: {result['session_name']}",
            f"Panes: {', '.join(panes) if panes else 'none'}",
        ]
    )


def format_run_state(state: dict) -> str:
    run_id = state.get("run_id", "unknown")
    status = state.get("status", "unknown")
    lines = [f"Run: {run_id}", f"Status: {status}"]

    if state.get("final_response"):
        lines.append(f"Final: {state['final_response']}")

    review_requests = state.get("review_requests", [])
    if review_requests:
        lines.append(f"Pending reviews: {len(review_requests)}")
        for review in review_requests:
            lines.append(f"  - {review['review_id']} task={review['task_id']} agent={review['agent_id']}")

    active_tasks = state.get("active_tasks", [])
    if active_tasks:
        lines.append(f"Active tasks: {len(active_tasks)}")
        for task in active_tasks:
            lines.append(f"  - {task['task_id']} {task['title']} [{task['status']}]")

    completed_tasks = state.get("completed_tasks", [])
    if completed_tasks:
        lines.append(f"Completed tasks: {len(completed_tasks)}")
        for task in completed_tasks:
            lines.append(f"  - {task['task_id']} {task['title']}")

    blocked_tasks = state.get("blocked_tasks", [])
    if blocked_tasks:
        lines.append(f"Blocked tasks: {len(blocked_tasks)}")
        for task in blocked_tasks:
            lines.append(f"  - {task['task_id']} {task['title']}")

    return "\n".join(lines)


def format_status(data: dict) -> str:
    lines = [
        f"Team: {data['team_id']}",
        f"Session: {data.get('session_name') or 'none'}",
    ]

    latest_run = data.get("latest_run")
    if latest_run:
        lines.extend(
            [
                f"Latest run: {latest_run['run_id']}",
                f"Run status: {latest_run['status']}",
                f"Request: {latest_run['request']}",
            ]
        )
        if latest_run.get("final_response"):
            lines.append(f"Final: {latest_run['final_response']}")
        checkpoint = latest_run.get("checkpoint")
        if checkpoint:
            lines.append(f"Checkpoint: next={checkpoint['next_node']} thread={checkpoint['thread_id']}")

    agents = data.get("agents", [])
    lines.append("Agents:")
    if agents:
        for agent in agents:
            task = agent.get("task") or "-"
            pane = agent.get("pane") or "-"
            lines.append(f"  - {agent['agent_id']} role={agent['role']} status={agent['status']} pane={pane} task={task}")
    else:
        lines.append("  - none")

    tasks = data.get("tasks", [])
    lines.append("Tasks:")
    if tasks:
        for task in tasks:
            summary = task.get("result_summary") or "-"
            lines.append(
                f"  - {task['task_id']} {task['title']} status={task['status']} agent={task.get('agent_id') or '-'} summary={summary}"
            )
    else:
        lines.append("  - none")

    reviews = data.get("pending_reviews", [])
    lines.append("Pending reviews:")
    if reviews:
        for review in reviews:
            lines.append(f"  - {review['review_id']} task={review['task_id']} agent={review['agent_id']} {review['summary']}")
    else:
        lines.append("  - none")

    runtime = data.get("runtime_issues", {})
    lines.append("Runtime issues:")
    if runtime:
        lines.append(
            "  - missing_panes={missing} stale_heartbeat={stale} orphaned_tasks={tasks} dangling_runs={runs}".format(
                missing=len(runtime.get("missing_panes", [])),
                stale=len(runtime.get("stale_heartbeat", [])),
                tasks=len(runtime.get("orphaned_tasks", [])),
                runs=len(runtime.get("dangling_runs", [])),
            )
        )
    else:
        lines.append("  - none")

    cleanup_candidates = data.get("cleanup_candidates", [])
    lines.append(f"Cleanup candidates: {len(cleanup_candidates)}")

    dirty_orphaned = data.get("dirty_orphaned_worktrees", [])
    lines.append(f"Dirty orphaned worktrees: {len(dirty_orphaned)}")

    conflicts = data.get("worktree_conflicts", [])
    lines.append(f"Worktree conflicts: {len(conflicts)}")

    retry_candidates = data.get("retry_candidates", [])
    lines.append(f"Retry candidates: {len(retry_candidates)}")
    return "\n".join(lines)


def format_reviews(reviews: list[dict]) -> str:
    if not reviews:
        return "Reviews\n  - none"
    lines = ["Reviews"]
    for review in reviews:
        lines.append(
            f"  - {review['review_id']} run={review['run_id']} task={review['task_id']} "
            f"agent={review['agent_id']} status={review['status']} summary={review['summary']}"
        )
    return "\n".join(lines)


def format_review_approval(data: dict) -> str:
    lines = [
        f"Review approved: {data['review_id']}",
        f"Status: {data['status']}",
    ]
    run_state = data.get("run_state")
    if run_state:
        lines.append(format_run_state(run_state))
    return "\n".join(lines)


def format_review_rejection(data: dict) -> str:
    lines = [
        f"Review rejected: {data['review_id']}",
        f"Status: {data['status']}",
    ]
    replan = data.get("replan")
    if replan:
        lines.extend(
            [
                f"Replan task: {replan['new_task_id']}",
                f"Assigned agent: {replan['assigned_agent_id']}",
                f"Reason: {replan['reason']}",
            ]
        )
    return "\n".join(lines)


def format_logs(events: list[dict]) -> str:
    if not events:
        return "Events\n  - none"
    lines = ["Events"]
    for event in events:
        summary = event.get("payload", {}).get("summary", "")
        parts = [f"#{event['event_id']}", event["event_type"]]
        if event.get("agent_id"):
            parts.append(f"agent={event['agent_id']}")
        if event.get("task_id"):
            parts.append(f"task={event['task_id']}")
        line = " ".join(parts)
        if summary:
            line = f"{line} :: {summary}"
        lines.append(f"  - {line}")
    return "\n".join(lines)


def format_recover(data: dict) -> str:
    lines = [
        "Recovery report",
        f"  - missing_panes: {len(data.get('missing_panes', []))}",
        f"  - stale_heartbeat: {len(data.get('stale_heartbeat', []))}",
        f"  - orphaned_tasks: {len(data.get('orphaned_tasks', []))}",
        f"  - orphaned_reviews: {len(data.get('orphaned_reviews', []))}",
        f"  - dangling_runs: {len(data.get('dangling_runs', []))}",
        f"  - reconciled_runs: {len(data.get('reconciled_runs', []))}",
        f"  - worktree_conflicts: {len(data.get('worktree_conflicts', []))}",
        f"  - cleanup_candidates: {len(data.get('cleanup_candidates', []))}",
        f"  - dirty_orphaned_worktrees: {len(data.get('dirty_orphaned_worktrees', []))}",
        "Actions taken:",
    ]
    actions = data.get("actions_taken", [])
    if actions:
        for action in actions:
            payload = ", ".join(f"{key}={value}" for key, value in action.items())
            lines.append(f"  - {payload}")
    else:
        lines.append("  - none")
    return "\n".join(lines)


def format_cleanup(data: dict) -> str:
    lines = [
        "Cleanup report",
        f"  - deleted_worktrees: {len(data.get('deleted_worktrees', []))}",
        f"  - skipped_dirty_worktrees: {len(data.get('skipped_dirty_worktrees', []))}",
        f"  - skipped_active_worktrees: {len(data.get('skipped_active_worktrees', []))}",
    ]
    return "\n".join(lines)


def format_outputs(outputs: list[dict]) -> str:
    if not outputs:
        return "Collected outputs\n  - none"
    lines = ["Collected outputs"]
    for output in outputs:
        lines.append(
            f"  - agent={output['agent_id']} task={output.get('task_id') or '-'} "
            f"kind={output['kind']} summary={output['summary']}"
        )
    return "\n".join(lines)
