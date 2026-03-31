from app.runtime import leader_console


class StubSupervisor:
    def __init__(self):
        self.calls = []

    def run(self, team_id: str, request: str):
        self.calls.append(("run", team_id, request))


def test_parse_command_splits_action_and_args():
    action, args = leader_console._parse_command("/status now")
    assert action == "/status"
    assert args == ["now"]


def test_run_leader_command_refresh_returns_none():
    supervisor = StubSupervisor()

    result = leader_console._run_leader_command("demo", "/refresh", supervisor)

    assert result is None
    assert supervisor.calls == []


def test_run_leader_command_dispatches_new_request():
    supervisor = StubSupervisor()

    result = leader_console._run_leader_command("demo", "investigate flaky tests", supervisor)

    assert result is None
    assert supervisor.calls == [("run", "demo", "investigate flaky tests")]
