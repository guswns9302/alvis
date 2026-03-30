from app.runtime.output_collector import OutputCollector


def test_output_collector_summarizes_recent_output():
    output = OutputCollector().summarize_task_output(
        agent_id="agent-1",
        task_id="task-1",
        log_text="line one\npytest passed\nM app/main.py\n",
    )
    assert output.kind == "final"
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

    assert output.kind == "final"
    assert output.summary == "API timeout handling updated."
    assert output.changed_files == ["app/api.py"]
    assert output.test_results == ["pytest tests/test_api.py -q"]


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
