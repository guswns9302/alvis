from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _event_summary(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    summary = payload.get("summary") or payload.get("output_summary") or payload.get("detail") or event.get("event_type") or "-"
    agent_id = event.get("agent_id") or "system"
    return f"[{agent_id}] {summary}"


def _workers_table(status: dict[str, Any]) -> Table:
    table = Table(expand=True, show_header=True, header_style="bold")
    table.add_column("Worker", ratio=2)
    table.add_column("Status", ratio=2)
    table.add_column("Task", ratio=5)
    for agent in status.get("agents", []):
        if agent.get("role") == "leader":
            continue
        table.add_row(
            str(agent.get("role_alias") or agent.get("role") or "-"),
            str(agent.get("status") or "-"),
            str(agent.get("task") or "-"),
        )
    if table.row_count == 0:
        table.add_row("-", "-", "-")
    return table


def render_dashboard(team_id: str, status: dict[str, Any], events: list[dict[str, Any]]) -> Panel:
    latest_run = status.get("latest_run") or {}
    summary = Table.grid(expand=True)
    summary.add_column(ratio=1)
    summary.add_column(ratio=3)
    summary.add_row("Team", team_id)
    summary.add_row("Run", str(latest_run.get("run_id") or "-"))
    summary.add_row("State", str(latest_run.get("status") or "-"))
    summary.add_row("Request", str(latest_run.get("request") or "-"))

    event_lines = events[-12:] if events else []
    timeline = Text("\n".join(_event_summary(event) for event in event_lines) or "No events yet.")
    candidate = status.get("final_output_candidate") or {}
    final_summary = candidate.get("summary") or latest_run.get("final_response") or "No final output yet."
    help_text = Text("/status  /logs  /clean  /quit  /shutdown", style="cyan")

    body = Group(
        Panel(summary, title="Run", border_style="blue"),
        Panel(_workers_table(status), title="Workers", border_style="green"),
        Panel(timeline, title="Timeline", border_style="yellow"),
        Panel(final_summary, title="Final Output", border_style="magenta"),
        Panel(help_text, title="Commands", border_style="cyan"),
    )
    return Panel(body, title=f"Alvis · {team_id}", border_style="bright_blue")


@dataclass
class ReplBackend:
    services: Any

    def status(self, team_id: str) -> dict[str, Any]:
        return self.services.status(team_id)

    def logs(self, team_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "agent_id": event.agent_id,
                "task_id": event.task_id,
                "payload": event.payload,
            }
            for event in self.services.list_events(team_id=team_id, run_id=run_id)
        ]

    def run_request(self, team_id: str, request: str) -> dict[str, Any]:
        from app.graph.supervisor import Supervisor, SupervisorDeps

        return Supervisor(SupervisorDeps(services=self.services)).run(team_id, request)

    def clean(self) -> dict[str, Any]:
        return self.services.clean_workspace_teams()

    def shutdown(self, team_id: str) -> dict[str, Any]:
        return self.services.remove_team(team_id)


def launch_repl(*, team_id: str, backend: ReplBackend) -> int:
    console = Console()
    while True:
        status = backend.status(team_id)
        latest_run = status.get("latest_run") or {}
        run_id = latest_run.get("run_id")
        events = backend.logs(team_id, run_id=run_id)
        console.clear()
        console.print(render_dashboard(team_id, status, events))
        command = console.input("\n[bold cyan]> [/]").strip()
        if not command:
            continue
        if command == "/quit":
            return 0
        if command == "/status":
            console.print_json(data=status)
            continue
        if command == "/logs":
            console.print_json(data=events[-20:])
            continue
        if command == "/clean":
            console.print_json(data=backend.clean())
            return 0
        if command == "/shutdown":
            console.print_json(data=backend.shutdown(team_id))
            return 0
        with console.status("Running request...", spinner="dots"):
            backend.run_request(team_id, command)
