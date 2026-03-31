from __future__ import annotations

import json
import subprocess

import typer
import uvicorn

from app.api.server import create_app
from app.bootstrap import bootstrap_services
from app.cli_formatters import (
    format_cleanup,
    format_logs,
    format_outputs,
    format_recover,
    format_review_approval,
    format_review_rejection,
    format_reviews,
    format_run_state,
    format_status,
    format_team_start,
)
from app.graph.supervisor import Supervisor, SupervisorDeps
from app.logging import configure_logging

app = typer.Typer(help="Alvis CLI")
team_app = typer.Typer(help="Team management")
review_app = typer.Typer(help="Review actions")
app.add_typer(team_app, name="team")
app.add_typer(review_app, name="review")


def _services():
    configure_logging()
    return bootstrap_services()


def _emit(data, json_output: bool, formatter) -> None:
    if json_output:
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo(formatter(data))


@app.command()
def bootstrap():
    configure_logging()
    subprocess.run(["bash", "scripts/bootstrap.sh"], check=True)


@team_app.command("create")
def create_team(team_id: str, workers: int = 2):
    services = _services()
    team = services.create_team(team_id, workers)
    typer.echo(f"created team {team.team_id}")


@team_app.command("start")
def start_team(team_id: str, json_output: bool = typer.Option(False, "--json")):
    services = _services()
    result = services.start_team(team_id)
    _emit(result, json_output, format_team_start)


@app.command()
def run(team_id: str, request: str, json_output: bool = typer.Option(False, "--json")):
    services = _services()
    supervisor = Supervisor(SupervisorDeps(services=services))
    state = supervisor.run(team_id, request)
    _emit(state, json_output, format_run_state)


@app.command()
def resume(run_id: str, json_output: bool = typer.Option(False, "--json")):
    services = _services()
    supervisor = Supervisor(SupervisorDeps(services=services))
    state = supervisor.resume(run_id)
    _emit(state, json_output, format_run_state)


@app.command()
def status(team_id: str, json_output: bool = typer.Option(False, "--json")):
    services = _services()
    _emit(services.status(team_id), json_output, format_status)


@review_app.command("list")
def list_reviews(json_output: bool = typer.Option(False, "--json")):
    services = _services()
    data = [
        {
            "review_id": review.review_id,
            "run_id": review.run_id,
            "task_id": review.task_id,
            "agent_id": review.agent_id,
            "status": review.status,
            "summary": review.summary,
        }
        for review in services.list_reviews()
    ]
    _emit(data, json_output, format_reviews)


@review_app.command("approve")
def approve_review(review_id: str, json_output: bool = typer.Option(False, "--json")):
    services = _services()
    review = services.resolve_review(review_id, approved=True)
    if not review:
        raise typer.Exit(code=1)
    supervisor = Supervisor(SupervisorDeps(services=services))
    state = supervisor.resume(review.run_id)
    payload = {"review_id": review.review_id, "status": review.status, "run_state": state}
    _emit(payload, json_output, format_review_approval)


@review_app.command("reject")
def reject_review(
    review_id: str,
    reason: str = "Rejected review requires follow-up task.",
    json_output: bool = typer.Option(False, "--json"),
):
    services = _services()
    review = services.resolve_review(review_id, approved=False, reason=reason)
    if not review:
        raise typer.Exit(code=1)
    replan = services.latest_replan_for_review(review.review_id)
    if replan:
        payload = {
            "review_id": review.review_id,
            "status": review.status,
            "replan": replan,
        }
        _emit(payload, json_output, format_review_rejection)
        return
    payload = {"review_id": review.review_id, "status": review.status}
    _emit(payload, json_output, format_review_rejection)


@app.command()
def logs(team_id: str, run_id: str | None = None, json_output: bool = typer.Option(False, "--json")):
    services = _services()
    data = [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "agent_id": event.agent_id,
            "task_id": event.task_id,
            "payload": event.payload,
        }
        for event in services.list_events(team_id=team_id, run_id=run_id)
    ]
    _emit(data, json_output, format_logs)


@app.command("collect-outputs")
def collect_outputs(team_id: str, json_output: bool = typer.Option(False, "--json")):
    services = _services()
    _emit(services.collect_outputs(team_id), json_output, format_outputs)


@app.command()
def recover(
    team_id: str | None = typer.Option(None, "--team-id"),
    retry: bool = typer.Option(False, "--retry"),
    json_output: bool = typer.Option(False, "--json"),
):
    services = _services()
    _emit(services.recover(team_id=team_id, retry=retry), json_output, format_recover)


@app.command()
def cleanup(team_id: str | None = typer.Option(None, "--team-id"), json_output: bool = typer.Option(False, "--json")):
    services = _services()
    _emit(services.cleanup_worktrees(team_id=team_id), json_output, format_cleanup)


@app.command("tmux-attach")
def tmux_attach(team_id: str):
    services = _services()
    raise typer.Exit(code=services.attach_tmux(team_id))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    configure_logging()
    uvicorn.run(create_app(), host=host, port=port)
