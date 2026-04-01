from app.runtime.output_collector import OutputCollector


def test_output_collector_summarizes_recent_output():
    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text="line one\npytest passed\nM app/main.py\n",
    )
    assert output.kind == "delta"
    assert output.summary == "M app/main.py"
    assert "pytest passed" in output.test_results
    assert "M app/main.py" in output.changed_files


def test_output_collector_prefers_structured_markers():
    log_text = """
    intermediate line
    ALVIS_RESULT_START
    SUMMARY: Billing test flake fixed by stabilizing retry window.
    CHANGED_FILES:
    - app/billing/retry.py
    - tests/test_billing_retry.py
    TEST_RESULTS:
    - pytest tests/test_billing_retry.py -q
    RISK_FLAGS:
    - Needs broader billing regression run before merge.
    ALVIS_RESULT_END
    """

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.kind == "final"
    assert output.summary == "Billing test flake fixed by stabilizing retry window."
    assert output.changed_files == ["app/billing/retry.py", "tests/test_billing_retry.py"]
    assert output.test_results == ["pytest tests/test_billing_retry.py -q"]
    assert output.risk_flags == ["Needs broader billing regression run before merge."]


def test_output_collector_uses_partial_marker_and_falls_back_for_missing_sections():
    log_text = """
    pytest tests/test_api.py -q
    M app/api.py
    ALVIS_RESULT_START
    SUMMARY: API timeout handling updated.
    CHANGED_FILES:
    - app/api.py
    """

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.kind == "delta"
    assert output.summary == "API timeout handling updated."
    assert output.changed_files == ["app/api.py"]
    assert output.test_results == ["pytest tests/test_api.py -q"]


def test_output_collector_rejects_invalid_status_signal():
    log_text = """
    ALVIS_RESULT_START
    STATUS: maybe
    SUMMARY: Invalid status should not pass validation.
    CHANGED_FILES:
    - app/main.py
    ALVIS_RESULT_END
    """

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.kind == "delta"
    assert output.summary == "No usable task output captured yet."


def test_output_collector_strips_terminal_noise_before_parsing():
    log_text = (
        "\x1b[?2004h]7;file://Users/okestro/work/git/alvis\x07\n"
        "\x1b[32mALVIS_RESULT_START\x1b[0m\n"
        "SUMMARY: Prompt formatting normalized.\n"
        "CHANGED_FILES:\n"
        "- app/agents/codex_adapter.py\n"
        "TEST_RESULTS:\n"
        "- pytest tests/test_task_prompt.py -q\n"
        "RISK_FLAGS:\n"
        "ALVIS_RESULT_END\n"
    )

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.summary == "Prompt formatting normalized."
    assert output.changed_files == ["app/agents/codex_adapter.py"]
    assert output.test_results == ["pytest tests/test_task_prompt.py -q"]
    assert output.risk_flags == []


def test_output_collector_ignores_shell_error_noise():
    log_text = """
    [ALVIS SESSION START]
    {"cmd": ["tmux", "list-panes"], "event": "tmux.command", "level": "info"}
    [ALVIS TASK]
    task_id: task-1
    role: implementer
    cwd: /repo
    zsh: command not found: role:
    zsh: command not found: cwd:
    """

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.kind == "delta"
    assert output.summary == "No usable task output captured yet."


def test_output_collector_parses_leader_interaction_sections():
    log_text = """
    ALVIS_RESULT_START
    STATUS: need_input
    SUMMARY: Need a leader decision before continuing.
    QUESTION_FOR_LEADER:
    - Which module owns the billing retry policy?
    REQUESTED_CONTEXT:
    - Need the current retry contract.
    FOLLOWUP_SUGGESTION:
    - Ask the backend implementer to update retry.md after clarification.
    DEPENDENCY_NOTE:
    - Reviewer is blocked until retry.md is updated.
    CHANGED_FILES:
    - docs/retry.md
    TEST_RESULTS:
    - none
    RISK_FLAGS:
    ALVIS_RESULT_END
    """

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.status_signal == "need_input"
    assert output.question_for_leader == ["Which module owns the billing retry policy?"]
    assert output.requested_context == ["Need the current retry contract."]
    assert output.followup_suggestion == ["Ask the backend implementer to update retry.md after clarification."]
    assert output.dependency_note == ["Reviewer is blocked until retry.md is updated."]


def test_output_collector_ignores_placeholder_result_template():
    log_text = """
    ALVIS_RESULT_START
    STATUS: <done|need_input|blocked|needs_review>
    SUMMARY: <one concise summary line>
    QUESTION_FOR_LEADER:
    - <question that requires leader guidance>
    REQUESTED_CONTEXT:
    - <missing context or dependency>
    FOLLOWUP_SUGGESTION:
    - <suggested next instruction>
    DEPENDENCY_NOTE:
    - <cross-agent dependency note>
    CHANGED_FILES:
    - <path or file summary>
    TEST_RESULTS:
    - <test result>
    RISK_FLAGS:
    - <risk or blocker>
    ALVIS_RESULT_END
    """

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.kind == "delta"
    assert output.summary == "No usable task output captured yet."


def test_output_collector_ignores_codex_startup_noise():
    log_text = """
    ╭──────────────────────────────────────────────╮
    │ >_ OpenAI Codex (v0.117.0)                   │
    │ model:     gpt-5.4 medium   /model to change │
    ╰──────────────────────────────────────────────╯
    Tip: New Try the Codex App with 2x rate limits until April 2nd.
    Run 'codex app' or visit
    https://chatgpt.com/codex?app-landing-page=true
    """

    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text=log_text,
    )

    assert output.kind == "delta"
    assert output.summary == "No usable task output captured yet."


def test_output_collector_prefers_final_message_text_over_stdout_noise():
    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text="stream noise\nstill running\n",
        final_message_text="""
        ALVIS_RESULT_START
        STATUS: done
        SUMMARY: Final assistant message captured from codex exec.
        CHANGED_FILES:
        - app/services.py
        TEST_RESULTS:
        - pytest tests/test_output_collector.py -q
        RISK_FLAGS:
        ALVIS_RESULT_END
        """,
    )

    assert output.kind == "final"
    assert output.output_parse_status == "ok"
    assert output.summary == "Final assistant message captured from codex exec."


def test_output_collector_prefers_schema_output_when_available():
    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text="noise only\n",
        schema_output_text="""
        {
          "status_signal": "done",
          "summary": "Schema output captured from codex exec.",
          "question_for_leader": [],
          "requested_context": [],
          "followup_suggestion": [],
          "dependency_note": [],
          "changed_files": ["app/services.py"],
          "test_results": ["pytest -q"],
          "risk_flags": []
        }
        """,
    )

    assert output.kind == "final"
    assert output.output_parse_status == "ok"
    assert output.summary == "Schema output captured from codex exec."
    assert output.changed_files == ["app/services.py"]


def test_output_collector_marks_invalid_schema_output():
    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text="noise only\n",
        schema_output_text='{"status_signal":"done","summary":42}',
    )

    assert output.kind == "delta"
    assert output.output_parse_status == "schema_contract_failed"
