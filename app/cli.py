from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import typer
import uvicorn

from app.api.server import create_app
from app.bootstrap import bootstrap_services
from app.cli_formatters import (
    format_clean,
    format_logs,
    format_outputs,
    format_recover,
    format_run_state,
    format_start,
    format_status,
    format_team_start,
)
from app.config import get_settings
from app.daemon_client import DaemonClient, DaemonHttpError, DaemonUnavailableError
from app.graph.supervisor import Supervisor, SupervisorDeps
from app.launchd import LaunchdManager
from app.logging import configure_logging
from app.upgrade import perform_upgrade
from app.version import __version__

app = typer.Typer(help="Alvis CLI")
daemon_app = typer.Typer(help="Daemon management")
app.add_typer(daemon_app, name="daemon")


def _workspace_root() -> Path:
    return Path(os.getenv("ALVIS_WORKSPACE_ROOT", Path.cwd())).expanduser().resolve()


def _direct_mode() -> bool:
    if os.getenv("ALVIS_DIRECT_MODE") == "1":
        return True
    return any(
        os.getenv(name)
        for name in (
            "PYTEST_CURRENT_TEST",
            "ALVIS_REPO_ROOT",
            "ALVIS_DB_PATH",
            "ALVIS_DATA_DIR",
        )
    )


def _services(workspace_root: str | Path | None = None):
    configure_logging()
    try:
        return bootstrap_services(workspace_root or _workspace_root())
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _daemon_client() -> DaemonClient:
    return DaemonClient(get_settings(_workspace_root()))


def _ensure_daemon_running() -> DaemonClient:
    client = _daemon_client()
    try:
        client.health()
        return client
    except DaemonUnavailableError:
        manager = LaunchdManager(get_settings(_workspace_root()))
        try:
            manager.start()
            client.health()
            return client
        except Exception as exc:  # pragma: no cover - runtime only
            typer.secho(f"failed to start alvis daemon: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc


def _emit(data, json_output: bool, formatter) -> None:
    if json_output:
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo(formatter(data))


@app.command()
def bootstrap():
    configure_logging()
    subprocess.run(["bash", "scripts/bootstrap.sh"], check=True)


@app.command()
def version(json_output: bool = typer.Option(False, "--json")):
    _emit({"version": __version__}, json_output, lambda data: f"alvis {data['version']}")


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json")):
    settings = get_settings(_workspace_root())
    client = _daemon_client()
    try:
        daemon = client.health(_workspace_root())
    except DaemonUnavailableError:
        daemon = {"status": "unreachable"}
    payload = {
        "version": __version__,
        "workspace_root": str(_workspace_root()),
        "app_home": str(settings.app_home),
        "data_dir": str(settings.data_dir),
        "daemon": daemon,
        "shell_tmux_path": settings.tmux_path,
        "shell_codex_command": settings.codex_command,
        "shell_tmux_available": subprocess.run(["which", "tmux"], check=False, capture_output=True, text=True).returncode == 0,
        "shell_codex_available": subprocess.run(["which", "codex"], check=False, capture_output=True, text=True).returncode == 0,
    }
    _emit(
        payload,
        json_output,
        lambda data: "\n".join(
            [
                f"alvis {data['version']}",
                f"workspace: {data['workspace_root']}",
                f"app_home: {data['app_home']}",
                f"data_dir: {data['data_dir']}",
                f"daemon: {data['daemon'].get('status', 'unknown')}",
                f"shell tmux_path: {data.get('shell_tmux_path') or '-'}",
                f"shell codex_command: {data.get('shell_codex_command') or '-'}",
                f"shell tmux: {'ok' if data['shell_tmux_available'] else 'missing'}",
                f"shell codex: {'ok' if data['shell_codex_available'] else 'missing'}",
                f"daemon tmux_path: {data['daemon'].get('daemon_tmux_path') or '-'}",
                f"daemon codex_command: {data['daemon'].get('daemon_codex_command') or '-'}",
                f"daemon tmux: {'ok' if data['daemon'].get('daemon_tmux_available') else 'missing'}",
            ]
        ),
    )


@app.command()
def start():
    try:
        if _direct_mode():
            services = _services()
            result = services.start_or_attach_default_team()
            typer.echo(format_start(result))
            raise typer.Exit(code=services.attach_tmux(result["team_id"]))
        else:
            client = _ensure_daemon_running()
            result = client.request_json("POST", "/start", payload=client.with_workspace(_workspace_root()), timeout=30)
            typer.echo(format_start(result))
            raise typer.Exit(code=_services().attach_tmux(result["team_id"]))
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except DaemonHttpError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail)}
        typer.secho(detail.get("detail") or str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def run(team_id: str, request: str, json_output: bool = typer.Option(False, "--json")):
    if _direct_mode():
        services = _services()
        supervisor = Supervisor(SupervisorDeps(services=services))
        state = supervisor.run(team_id, request)
    else:
        client = _ensure_daemon_running()
        state = client.request_json("POST", "/runs", payload={**client.with_workspace(_workspace_root()), "team_id": team_id, "request": request})
    _emit(state, json_output, format_run_state)


@app.command()
def resume(run_id: str, json_output: bool = typer.Option(False, "--json")):
    if _direct_mode():
        services = _services()
        supervisor = Supervisor(SupervisorDeps(services=services))
        state = supervisor.resume(run_id)
    else:
        client = _ensure_daemon_running()
        state = client.request_json("POST", f"/runs/{run_id}/resume", payload=client.with_workspace(_workspace_root()))
    _emit(state, json_output, format_run_state)


@app.command()
def status(team_id: str, json_output: bool = typer.Option(False, "--json")):
    try:
        if _direct_mode():
            result = _services().status(team_id)
        else:
            client = _ensure_daemon_running()
            result = client.request_json("GET", "/status", query={**client.with_workspace(_workspace_root()), "team_id": team_id})
        _emit(result, json_output, format_status)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def logs(team_id: str, run_id: str | None = None, json_output: bool = typer.Option(False, "--json")):
    if _direct_mode():
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
    else:
        client = _ensure_daemon_running()
        data = client.request_json("GET", "/logs", query={**client.with_workspace(_workspace_root()), "team_id": team_id, "run_id": run_id})
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
    if _direct_mode():
        result = _services().recover(team_id=team_id, retry=retry)
    else:
        client = _ensure_daemon_running()
        result = client.request_json("POST", "/recover", payload={**client.with_workspace(_workspace_root()), "team_id": team_id, "retry": retry})
    _emit(result, json_output, format_recover)


@app.command()
def clean(json_output: bool = typer.Option(False, "--json")):
    if _direct_mode():
        result = _services().clean_workspace_teams()
    else:
        client = _ensure_daemon_running()
        result = client.request_json("POST", "/clean", payload=client.with_workspace(_workspace_root()))
    _emit(result, json_output, format_clean)


@app.command("tmux-attach")
def tmux_attach(team_id: str):
    services = _services()
    raise typer.Exit(code=services.attach_tmux(team_id))


@app.command()
def upgrade(version: str | None = typer.Option(None, "--version"), json_output: bool = typer.Option(False, "--json")):
    result = perform_upgrade(get_settings(_workspace_root()), version)
    _emit(
        result,
        json_output,
        lambda data: "\n".join(
            [
                f"status: {data['status']}",
                f"current_version: {data['current_version']}",
                f"target_version: {data['target_version']}",
            ]
        ),
    )


@daemon_app.command("status")
def daemon_status(json_output: bool = typer.Option(False, "--json")):
    payload = LaunchdManager(get_settings(_workspace_root())).status()
    _emit(payload, json_output, lambda data: f"label: {data['label']}\nrunning: {data['running']}")


@daemon_app.command("start")
def daemon_start(json_output: bool = typer.Option(False, "--json")):
    payload = LaunchdManager(get_settings(_workspace_root())).start()
    _emit(payload, json_output, lambda data: f"label: {data['label']}\nstatus: {data['status']}")


@daemon_app.command("stop")
def daemon_stop(json_output: bool = typer.Option(False, "--json")):
    payload = LaunchdManager(get_settings(_workspace_root())).stop()
    _emit(payload, json_output, lambda data: f"label: {data['label']}\nstatus: {data['status']}")


@daemon_app.command("restart")
def daemon_restart(json_output: bool = typer.Option(False, "--json")):
    payload = LaunchdManager(get_settings(_workspace_root())).restart()
    _emit(payload, json_output, lambda data: f"label: {data['label']}\nstatus: {data['status']}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    configure_logging()
    uvicorn.run(create_app(), host=host, port=port)
