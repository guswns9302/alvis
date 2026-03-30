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
