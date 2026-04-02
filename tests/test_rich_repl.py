from __future__ import annotations

from rich.console import Console

from app import rich_repl


def _sample_status() -> dict:
    return {
        "team_id": "team-demo",
        "agents": [
            {
                "agent_id": "team-demo-leader",
                "role": "leader",
                "role_alias": "leader",
                "status": "idle",
                "task": None,
            },
            {
                "agent_id": "team-demo-worker-1",
                "role": "implementer",
                "role_alias": "executor",
                "status": "running",
                "task": "task-1",
            },
            {
                "agent_id": "team-demo-worker-2",
                "role": "reviewer",
                "role_alias": "reviewer",
                "status": "idle",
                "task": None,
            },
        ],
        "latest_run": {
            "run_id": "run-1",
            "status": "running",
            "request": "파이썬과 자바 차이를 알려줘",
            "final_response": None,
        },
        "tasks": [
            {
                "task_id": "task-1",
                "title": "파이썬과 자바 차이 조사",
                "goal": "비교",
                "latest_output": {"summary": "조사 중"},
            }
        ],
        "final_output_candidate": {},
    }


def test_render_worker_strip_shows_compact_worker_state():
    console = Console(record=True, width=120)

    console.print(rich_repl.render_worker_strip(_sample_status()))
    output = console.export_text()

    assert "executor" in output
    assert "running" in output
    assert "파이썬과 자바 차이 조사" in output
    assert "reviewer" in output
    assert "╭" not in output
    assert "workers" in output
    assert "|" in output


def test_render_event_message_uses_role_alias_and_summary():
    console = Console(record=True, width=120)
    event = {
        "event_id": "evt-1",
        "event_type": "task.assigned",
        "agent_id": "team-demo-worker-1",
        "payload": {"summary": "초안 작성을 시작했습니다."},
    }

    console.print(rich_repl.render_event_message(event, _sample_status()))
    output = console.export_text()

    assert "Executor" in output
    assert "Executor가 작업을 시작했습니다." in output
    assert "│" not in output


def test_worker_voice_message_formats_structured_output_event():
    status = _sample_status()
    event = {
        "event_id": "evt-2",
        "event_type": "agent.output.final",
        "agent_id": "team-demo-worker-1",
        "task_id": "task-1",
        "payload": {
            "summary": "파이썬은 동적 타입, 자바는 정적 타입 중심입니다.",
            "status_signal": "done",
        },
    }

    assert rich_repl._worker_voice_message(event, status) == "파이썬은 동적 타입, 자바는 정적 타입 중심입니다."


def test_worker_voice_message_formats_parse_failure_for_user():
    status = _sample_status()
    event = {
        "event_id": "evt-3",
        "event_type": "agent.output.final",
        "agent_id": "team-demo-worker-1",
        "task_id": "task-1",
        "payload": {
            "summary": "Task did not produce a valid structured result block.",
            "status_signal": "blocked",
            "output_parse_status": "schema_contract_failed",
        },
    }

    assert rich_repl._worker_voice_message(event, status) == "구조화된 응답이 기대 계약과 맞지 않습니다."


def test_worker_voice_message_formats_runtime_failure_with_hint():
    status = _sample_status()
    event = {
        "event_id": "evt-4",
        "event_type": "error.raised",
        "agent_id": "team-demo-worker-1",
        "task_id": "task-1",
        "payload": {
            "summary": "Task execution via background runner needs attention",
            "error_summary": "Codex 실행 옵션이 현재 설치된 Codex 버전과 맞지 않아 실행에 실패했습니다.",
            "error_hint": "Codex 버전을 확인한 뒤 다시 시도하세요.",
            "exit_code": 1,
        },
    }

    message = rich_repl._worker_voice_message(event, status)
    assert "Codex 실행 옵션이 현재 설치된 Codex 버전과 맞지 않아 실행에 실패했습니다." in message
    assert "exit=1" in message
    assert "Codex 버전을 확인한 뒤 다시 시도하세요." in message


def test_sync_transcript_emits_final_response_once():
    console = Console(record=True, width=120)
    status = _sample_status()
    status["latest_run"]["final_response"] = "최종 응답입니다."
    events = [
        {
            "event_id": "evt-1",
            "event_type": "leader.output.ready",
            "agent_id": "team-demo-worker-1",
            "payload": {"summary": "리더 응답 준비 완료"},
        }
    ]
    seen_event_ids: set[str] = set()
    seen_worker_output_keys: set[tuple[str, str, str, str, str, str]] = set()
    shown_final_keys: set[tuple[str, str]] = set()

    rich_repl._sync_transcript(
        console,
        status=status,
        events=events,
        seen_event_ids=seen_event_ids,
        seen_worker_output_keys=seen_worker_output_keys,
        shown_final_keys=shown_final_keys,
    )
    rich_repl._sync_transcript(
        console,
        status=status,
        events=events,
        seen_event_ids=seen_event_ids,
        seen_worker_output_keys=seen_worker_output_keys,
        shown_final_keys=shown_final_keys,
    )

    output = console.export_text()
    assert output.count("최종 응답 초안을 전달했습니다.") == 1
    assert output.count("최종 응답입니다.") == 1


def test_sync_transcript_dedupes_repeated_worker_output_messages():
    console = Console(record=True, width=120)
    status = _sample_status()
    events = [
        {
            "event_id": "evt-1",
            "event_type": "agent.output.delta",
            "agent_id": "team-demo-worker-1",
            "task_id": "task-1",
            "payload": {"summary": "자료를 조사 중입니다."},
        },
        {
            "event_id": "evt-2",
            "event_type": "agent.output.delta",
            "agent_id": "team-demo-worker-1",
            "task_id": "task-1",
            "payload": {"summary": "자료를 조사 중입니다."},
        },
    ]
    seen_event_ids: set[str] = set()
    seen_worker_output_keys: set[tuple[str, str, str, str, str, str]] = set()
    shown_final_keys: set[tuple[str, str]] = set()

    rich_repl._sync_transcript(
        console,
        status=status,
        events=events,
        seen_event_ids=seen_event_ids,
        seen_worker_output_keys=seen_worker_output_keys,
        shown_final_keys=shown_final_keys,
    )

    output = console.export_text()
    assert output.count("자료를 조사 중입니다.") == 1


def test_print_prompt_context_shows_pending_question():
    console = Console(record=True, width=120)
    status = _sample_status()
    status["pending_interactions"] = [
        {
            "interaction_id": "interaction-1",
            "kind": "intent_clarification",
            "message": "어느 섹션부터 수정해야 하나요?",
        }
    ]

    rich_repl._print_prompt_context(console, status=status, last_worker_signature=None, last_banner=None)
    output = console.export_text()

    assert "어느 섹션부터 수정해야 하나요?" in output
    assert "Alvis needs a quick clarification" in output
    assert "╭" not in output


def test_friendly_background_error_rewrites_graph_recursion_message():
    exc = RuntimeError(
        "Recursion limit of 25 reached without hitting a stop condition. "
        "For troubleshooting, visit: https://python.langchain.com/docs/troubleshooting/errors/GRAPH_RECURSION_LIMIT"
    )

    message = rich_repl._friendly_background_error(exc)

    assert "워커 결과를 수집하는 중" in message
    assert "GRAPH_RECURSION_LIMIT" not in message
