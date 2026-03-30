from app.bootstrap import bootstrap_services
from app.graph.supervisor import Supervisor, SupervisorDeps


def test_supervisor_creates_run_and_tasks():
    services = bootstrap_services()
    services.create_team("test-team", 2)
    state = Supervisor(SupervisorDeps(services=services)).run("test-team", "fix a bug")
    assert state["run_id"].startswith("run-")
    assert len(state["tasks"]) == 3
