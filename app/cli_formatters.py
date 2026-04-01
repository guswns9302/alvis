from __future__ import annotations


def _section(title: str, lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "  - none"
    return f"{title}\n{body}"


def format_team_start(result: dict) -> str:
    panes = result.get("panes", [])
    lines = [
        f"팀 시작됨: {result['team_id']}",
        f"세션: {result['session_name']}",
        f"패널: {', '.join(panes) if panes else '없음'}",
    ]
    issues = result.get("session_issues", [])
    if issues:
        lines.append("세션 경고:")
        for issue in issues:
            detail = issue.get("error_summary") or issue.get("runtime_status") or "알 수 없는 오류"
            lines.append(f"  - {issue['agent_id']}: {detail}")
            if issue.get("error_hint"):
                lines.append(f"    힌트: {issue['error_hint']}")
    return "\n".join(lines)


def format_team_create(result: dict) -> str:
    lines = [
        f"팀 생성됨: {result['team_id']}",
        "워커 역할:",
    ]
    for worker in result.get("workers", []):
        lines.append(f"  - {worker['agent_id']} role={worker['role']} alias={worker['role_alias']}")
    start_result = result.get("start_result")
    if start_result:
        lines.append(format_team_start(start_result))
    return "\n".join(lines)


def format_team_remove(result: dict) -> str:
    lines = [
        f"팀 제거: {result['team_id']}",
        f"제거됨: {'예' if result.get('removed') else '아니오'}",
        f"세션: {result.get('session_name') or '없음'}",
    ]
    removed_dirs = result.get("removed_agent_runtime_dirs", [])
    lines.append(f"정리된 런타임 디렉터리: {len(removed_dirs)}")
    return "\n".join(lines)


def format_start(result: dict) -> str:
    if result.get("action") == "attached_existing":
        return "\n".join(
            [
                f"기존 팀 세션 진입: {result['team_id']}",
                f"세션: {result['session_name']}",
            ]
        )
    start_result = {
        "team_id": result.get("team_id"),
        "session_name": result.get("session_name"),
        **(result.get("start_result") or {}),
    }
    lines = [
        f"새 팀 시작: {result['team_id']}",
        f"세션: {result.get('session_name') or start_result.get('session_name') or '없음'}",
    ]
    if start_result:
        lines.append(format_team_start(start_result))
    return "\n".join(lines)


def format_clean(result: dict) -> str:
    lines = [
        f"removed_teams: {result.get('removed_count', 0)}",
        f"skipped_teams: {result.get('skipped_count', 0)}",
    ]
    for team in result.get("removed_teams", []):
        lines.append(f"  - removed {team['team_id']} session={team.get('session_name') or '-'}")
    for team in result.get("skipped_teams", []):
        lines.append(f"  - skipped {team['team_id']} session={team.get('session_name') or '-'}")
    return "\n".join(lines)


def format_run_state(state: dict) -> str:
    run_id = state.get("run_id", "unknown")
    status = state.get("status", "unknown")
    lines = [f"실행: {run_id}", f"상태: {status}"]

    if state.get("final_response"):
        lines.append(f"최종 응답: {state['final_response']}")

    handoffs = state.get("handoffs", [])
    if handoffs:
        lines.append(f"자동 handoff: {len(handoffs)}")
        for task in handoffs:
            lines.append(f"  - {task['task_id']} {task['title']} [{task['status']}]")

    active_tasks = state.get("active_tasks", [])
    if active_tasks:
        lines.append(f"진행 중 작업: {len(active_tasks)}")
        for task in active_tasks:
            lines.append(f"  - {task['task_id']} {task['title']} [{task['status']}]")

    completed_tasks = state.get("completed_tasks", [])
    if completed_tasks:
        lines.append(f"완료된 작업: {len(completed_tasks)}")
        for task in completed_tasks:
            lines.append(f"  - {task['task_id']} {task['title']}")

    blocked_tasks = state.get("blocked_tasks", [])
    if blocked_tasks:
        lines.append(f"차단된 작업: {len(blocked_tasks)}")
        for task in blocked_tasks:
            lines.append(f"  - {task['task_id']} {task['title']}")

    return "\n".join(lines)


def format_status(data: dict) -> str:
    lines = [
        f"팀: {data['team_id']}",
        f"세션: {data.get('session_name') or '없음'}",
    ]

    latest_run = data.get("latest_run")
    if latest_run:
        lines.extend(
            [
                f"최근 실행: {latest_run['run_id']}",
                f"실행 상태: {latest_run['status']}",
                f"요청: {latest_run['request']}",
            ]
        )
        if latest_run.get("final_response"):
            lines.append(f"최종 응답: {latest_run['final_response']}")
        checkpoint = latest_run.get("checkpoint")
        if checkpoint:
            lines.append(f"체크포인트: next={checkpoint['next_node']} thread={checkpoint['thread_id']}")

    agents = data.get("agents", [])
    lines.append("에이전트:")
    if agents:
        for agent in agents:
            task = agent.get("task") or "-"
            pane = agent.get("pane") or "-"
            runtime_health = agent.get("runtime_health", {})
            runtime_status = runtime_health.get("status", "unknown")
            lines.append(
                f"  - {agent['agent_id']} role={agent.get('role_alias') or agent['role']} status={agent['status']} "
                f"pane={pane} task={task} runtime={runtime_status}"
            )
            if runtime_health.get("error_summary"):
                lines.append(f"    오류: {runtime_health['error_summary']}")
            if runtime_health.get("error_hint"):
                lines.append(f"    힌트: {runtime_health['error_hint']}")
    else:
        lines.append("  - 없음")

    tasks = data.get("tasks", [])
    lines.append("작업:")
    if tasks:
        for task in tasks:
            summary = task.get("result_summary") or "-"
            lines.append(
                f"  - {task['task_id']} {task['title']} status={task['status']} agent={task.get('agent_id') or '-'} "
                f"role={task.get('target_role_alias') or '-'} paths={', '.join(task.get('owned_paths', [])) or '-'} summary={summary}"
            )
    else:
        lines.append("  - 없음")

    handoffs = data.get("handoffs", [])
    lines.append("자동 handoff:")
    if handoffs:
        for item in handoffs:
            lines.append(
                f"  - {item['task_id']} parent={item.get('parent_task_id') or '-'} "
                f"agent={item.get('agent_id') or '-'} role={item.get('target_role_alias') or '-'} status={item.get('status') or '-'}"
            )
    else:
        lines.append("  - 없음")

    candidate = data.get("final_output_candidate")
    lines.append("최종 출력 후보:")
    if candidate:
        lines.append(
            f"  - ready={'yes' if data.get('final_output_ready') else 'no'} "
            f"task={candidate.get('task_id') or '-'} agent={candidate.get('agent_id') or '-'} summary={candidate.get('summary') or '-'}"
        )
    else:
        lines.append("  - 없음")

    redo_tasks = data.get("redo_tasks", [])
    lines.append("재작업 작업:")
    if redo_tasks:
        for task in redo_tasks:
            lines.append(
                f"  - {task['task_id']} parent={task.get('parent_task_id') or '-'} "
                f"agent={task.get('agent_id') or '-'} role={task.get('target_role_alias') or '-'} status={task.get('status') or '-'}"
            )
    else:
        lines.append("  - 없음")

    interactions = data.get("pending_interactions", [])
    lines.append("대기 중인 상호작용:")
    if interactions:
        for item in interactions:
            message = item.get("payload", {}).get("message") or item["kind"]
            lines.append(
                f"  - {item['interaction_id']} kind={item['kind']} source={item.get('source_agent_id') or '-'} "
                f"task={item.get('task_id') or '-'} message={message}"
            )
    else:
        lines.append("  - 없음")

    leader_queue = data.get("leader_queue", [])
    lines.append(f"리더 큐: {len(leader_queue)}")

    runtime = data.get("runtime_issues", {})
    lines.append("런타임 이슈:")
    if runtime:
        lines.append(
            "  - missing_panes={missing} stale_heartbeat={stale} session_not_ready={not_ready} session_exited={exited} orphaned_tasks={tasks} dangling_runs={runs}".format(
                missing=len(runtime.get("missing_panes", [])),
                stale=len(runtime.get("stale_heartbeat", [])),
                not_ready=len(runtime.get("session_not_ready", [])),
                exited=len(runtime.get("session_exited", [])),
                tasks=len(runtime.get("orphaned_tasks", [])),
                runs=len(runtime.get("dangling_runs", [])),
            )
        )
        session_errors = data.get("agents", [])
        for agent in session_errors:
            runtime_health = agent.get("runtime_health", {})
            if runtime_health.get("error_summary"):
                lines.append(f"  - {agent['agent_id']}: {runtime_health['error_summary']}")
    else:
        lines.append("  - 없음")

    cleanup_candidates = data.get("cleanup_candidates", [])
    lines.append(f"정리 후보 에이전트: {len(cleanup_candidates)}")

    conflicts = data.get("scope_conflicts", [])
    lines.append(f"파일 범위 충돌: {len(conflicts)}")

    retry_candidates = data.get("retry_candidates", [])
    lines.append(f"재시도 가능 작업: {len(retry_candidates)}")
    return "\n".join(lines)


def format_reviews(reviews: list[dict]) -> str:
    if not reviews:
        return "리뷰\n  - 없음"
    lines = ["리뷰"]
    for review in reviews:
        lines.append(
            f"  - {review['review_id']} run={review['run_id']} task={review['task_id']} "
            f"agent={review['agent_id']} status={review['status']} summary={review['summary']}"
        )
    return "\n".join(lines)


def format_review_approval(data: dict) -> str:
    lines = [
        f"리뷰 승인: {data['review_id']}",
        f"상태: {data['status']}",
    ]
    run_state = data.get("run_state")
    if run_state:
        lines.append(format_run_state(run_state))
    return "\n".join(lines)


def format_review_rejection(data: dict) -> str:
    lines = [
        f"리뷰 거절: {data['review_id']}",
        f"상태: {data['status']}",
    ]
    replan = data.get("replan")
    if replan:
        lines.extend(
            [
                f"재계획 작업: {replan['new_task_id']}",
                f"할당 에이전트: {replan['assigned_agent_id']}",
                f"사유: {replan['reason']}",
            ]
        )
    return "\n".join(lines)


def format_logs(events: list[dict]) -> str:
    if not events:
        return "이벤트\n  - 없음"
    lines = ["이벤트"]
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
        "복구 보고서",
        f"  - missing_panes: {len(data.get('missing_panes', []))}",
        f"  - stale_heartbeat: {len(data.get('stale_heartbeat', []))}",
        f"  - session_not_ready: {len(data.get('session_not_ready', []))}",
        f"  - session_exited: {len(data.get('session_exited', []))}",
        f"  - orphaned_tasks: {len(data.get('orphaned_tasks', []))}",
        f"  - orphaned_reviews: {len(data.get('orphaned_reviews', []))}",
        f"  - dangling_runs: {len(data.get('dangling_runs', []))}",
        f"  - reconciled_runs: {len(data.get('reconciled_runs', []))}",
        f"  - scope_conflicts: {len(data.get('scope_conflicts', []))}",
        f"  - cleanup_candidates: {len(data.get('cleanup_candidates', []))}",
        "수행한 조치:",
    ]
    actions = data.get("actions_taken", [])
    if actions:
        for action in actions:
            payload = ", ".join(f"{key}={value}" for key, value in action.items())
            lines.append(f"  - {payload}")
    else:
        lines.append("  - 없음")
    session_errors = data.get("session_errors", [])
    if session_errors:
        lines.append("감지된 세션 오류:")
        for item in session_errors:
            lines.append(f"  - {item['agent_id']}: {item['error_summary']}")
            if item.get("error_hint"):
                lines.append(f"    힌트: {item['error_hint']}")
    return "\n".join(lines)


def format_cleanup(data: dict) -> str:
    lines = [
        "정리 보고서",
        f"  - deleted_runtime_dirs: {len(data.get('deleted_runtime_dirs', []))}",
        f"  - skipped_active_agents: {len(data.get('skipped_active_agents', []))}",
    ]
    return "\n".join(lines)


def format_outputs(outputs: list[dict]) -> str:
    if not outputs:
        return "수집된 출력\n  - 없음"
    lines = ["수집된 출력"]
    for output in outputs:
        lines.append(
            f"  - agent={output['agent_id']} task={output.get('task_id') or '-'} "
            f"kind={output['kind']} summary={output['summary']}"
        )
    return "\n".join(lines)
