from __future__ import annotations

import json
import subprocess

import typer
import uvicorn

from app.api.server import create_app
from app.bootstrap import bootstrap_services
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
def start_team(team_id: str):
    services = _services()
    result = services.start_team(team_id)
    typer.echo(json.dumps(result, indent=2))


@app.command()
def run(team_id: str, request: str):
    services = _services()
    supervisor = Supervisor(SupervisorDeps(services=services))
    state = supervisor.run(team_id, request)
    typer.echo(json.dumps(state, indent=2))


@app.command()
def status(team_id: str):
    services = _services()
    typer.echo(json.dumps(services.status(team_id), indent=2))


@review_app.command("list")
def list_reviews():
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
    typer.echo(json.dumps(data, indent=2))


@review_app.command("approve")
def approve_review(review_id: str):
    services = _services()
    review = services.resolve_review(review_id, approved=True)
    if not review:
        raise typer.Exit(code=1)
    typer.echo(f"approved {review.review_id}")


@review_app.command("reject")
def reject_review(review_id: str, reason: str = "Rejected review requires follow-up task."):
    services = _services()
    review = services.resolve_review(review_id, approved=False, reason=reason)
    if not review:
        raise typer.Exit(code=1)
    replan = services.latest_replan_for_review(review.review_id)
    if replan:
        typer.echo(
            json.dumps(
                {
                    "review_id": review.review_id,
                    "status": review.status,
                    "replan": replan,
                },
                indent=2,
            )
        )
        return
    typer.echo(f"rejected {review.review_id}")


@app.command()
def logs(team_id: str, run_id: str | None = None):
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
    typer.echo(json.dumps(data, indent=2))


@app.command("collect-outputs")
def collect_outputs(team_id: str):
    services = _services()
    typer.echo(json.dumps(services.collect_outputs(team_id), indent=2))


@app.command()
def recover(team_id: str | None = None):
    services = _services()
    typer.echo(json.dumps(services.recover(team_id=team_id), indent=2))


@app.command("tmux-attach")
def tmux_attach(team_id: str):
    services = _services()
    raise typer.Exit(code=services.attach_tmux(team_id))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    configure_logging()
    uvicorn.run(create_app(), host=host, port=port)
