from __future__ import annotations

import json
import os
import subprocess
import sys
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
)
from app.config import get_settings
from app.daemon_client import DaemonClient, DaemonHttpError, DaemonUnavailableError
from app.graph.supervisor import Supervisor, SupervisorDeps
from app.install_paths import inspect_installation_state
from app.launchd import LaunchdManager
from app.logging import configure_logging
from app.rich_repl import ReplBackend, launch_repl
from app.runtime.codex_sdk_runtime import verify_codex_sdk_runtime
from app.upgrade import perform_upgrade
from app.version import __version__

app = typer.Typer(help="Alvis CLI")
daemon_app = typer.Typer(help="Daemon management")
app.add_typer(daemon_app, name="daemon")


def _normalize_version(value: str | None) -> str | None:
    if value is None:
        return None
    return value[1:] if value.startswith("v") else value


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
    install_state = inspect_installation_state(settings)
    try:
        daemon = client.health(_workspace_root())
    except DaemonUnavailableError:
        daemon = {"status": "unreachable"}
    sdk_runtime = verify_codex_sdk_runtime(settings) if settings.worker_backend == "codex-sdk" else {
        "node_available": subprocess.run(["which", "node"], check=False, capture_output=True, text=True).returncode == 0,
        "npm_available": subprocess.run(["which", "npm"], check=False, capture_output=True, text=True).returncode == 0,
        "sdk_installed": False,
        "sdk_import_error": None,
    }
    payload = {
        "version": __version__,
        "workspace_root": str(_workspace_root()),
        "app_home": str(settings.app_home),
        "data_dir": str(settings.data_dir),
        "db_path": str(settings.db_path),
        "runtime_dir": str(settings.runtime_dir),
        "daemon": daemon,
        "worker_backend": settings.worker_backend,
        "worker_model": settings.worker_model,
        "node_available": sdk_runtime.get("node_available"),
        "npm_available": sdk_runtime.get("npm_available"),
        "codex_sdk_package_available": sdk_runtime.get("sdk_installed"),
        "codex_api_key_configured": bool(settings.codex_api_key),
        "shell_codex_command": settings.codex_command,
        "shell_codex_available": subprocess.run(["which", "codex"], check=False, capture_output=True, text=True).returncode == 0,
        "install_metadata_version": install_state.get("metadata_version"),
        "installed_app_version": install_state.get("installed_app_version"),
        "install_drift_detected": _normalize_version(install_state.get("metadata_version")) != _normalize_version(install_state.get("installed_app_version")),
    }
    daemon_version = payload["daemon"].get("version")
    daemon_version_matches = _normalize_version(daemon_version) == _normalize_version(__version__) if daemon_version else None
    payload["daemon_version_matches"] = daemon_version_matches
    payload["install_version_matches"] = _normalize_version(payload["install_metadata_version"]) == _normalize_version(payload["installed_app_version"])
    if payload["worker_backend"] == "codex-sdk" and not payload["node_available"]:
        next_action = "install node and rerun `alvis doctor`"
    elif payload["worker_backend"] == "codex-sdk" and not payload["npm_available"]:
        next_action = "install npm and rerun `alvis doctor`"
    elif payload["worker_backend"] == "codex-sdk" and not payload["codex_sdk_package_available"]:
        next_action = "run `alvis upgrade` to install Codex SDK dependencies"
    elif payload["worker_backend"] == "codex-sdk" and not payload["codex_api_key_configured"]:
        next_action = "export CODEX_API_KEY and rerun `alvis doctor`"
    elif payload["worker_backend"] != "codex-sdk" and not payload["shell_codex_available"]:
        next_action = "install codex and rerun `alvis doctor`"
    elif payload["install_drift_detected"]:
        next_action = "run `alvis upgrade` to repair the installed app state"
    elif daemon_version_matches is False:
        next_action = "run `alvis daemon restart` or `alvis upgrade` again"
    elif payload["daemon"].get("status") != "ok":
        next_action = "run `alvis daemon restart` and verify daemon health"
    else:
        next_action = "run `alvis start`"
    payload["recommended_action"] = next_action
    _emit(
        payload,
        json_output,
        lambda data: "\n".join(
            [
                f"alvis {data['version']}",
                f"workspace: {data['workspace_root']}",
                f"app_home: {data['app_home']}",
                f"data_dir: {data['data_dir']}",
                f"db_path: {data['db_path']}",
                f"runtime_dir: {data['runtime_dir']}",
                f"daemon: {data['daemon'].get('status', 'unknown')}",
                f"worker backend: {data.get('worker_backend') or '-'}",
                f"worker model: {data.get('worker_model') or '-'}",
                f"node: {'ok' if data.get('node_available') else 'missing'}",
                f"npm: {'ok' if data.get('npm_available') else 'missing'}",
                f"codex sdk package: {'ok' if data.get('codex_sdk_package_available') else 'missing'}",
                f"codex api key: {'ok' if data.get('codex_api_key_configured') else 'missing'}",
                f"shell codex_command: {data.get('shell_codex_command') or '-'}",
                f"shell codex: {'ok' if data['shell_codex_available'] else 'missing'}",
                f"install metadata version: {data.get('install_metadata_version') or '-'}",
                f"installed app version: {data.get('installed_app_version') or '-'}",
                "install drift detected"
                if data.get("install_drift_detected")
                else "install drift: none",
                f"daemon version: {data['daemon'].get('version') or '-'}",
                f"daemon codex_command: {data['daemon'].get('daemon_codex_command') or '-'}",
                f"daemon worker backend: {data['daemon'].get('daemon_worker_backend') or '-'}",
                f"daemon worker model: {data['daemon'].get('daemon_worker_model') or '-'}",
                f"daemon workspace: {data['daemon'].get('daemon_workspace_root') or '-'}",
                f"daemon data_dir: {data['daemon'].get('daemon_data_dir') or '-'}",
                f"daemon db_path: {data['daemon'].get('daemon_db_path') or '-'}",
                f"daemon runtime_dir: {data['daemon'].get('daemon_runtime_dir') or '-'}",
                f"workspace teams: {data['daemon'].get('daemon_team_count') if data['daemon'].get('daemon_team_count') is not None else '-'}",
                (
                    "daemon version mismatch: run `alvis daemon restart` or `alvis upgrade` again"
                    if data.get("daemon_version_matches") is False
                    else "daemon version matches cli"
                    if data.get("daemon_version_matches") is True
                    else "daemon version match: unknown"
                ),
                f"recommended action: {data.get('recommended_action') or '-'}",
            ]
        ),
    )


@app.command()
def start():
    try:
        services = _services()
        result = services.start_or_attach_default_team()
        typer.echo(format_start(result))
        if os.getenv("PYTEST_CURRENT_TEST") or not sys.stdin.isatty():
            raise typer.Exit(code=0)
        raise typer.Exit(code=launch_repl(team_id=result["team_id"], backend=ReplBackend(services=services)))
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
        result = client.request_json("POST", "/clean", payload=client.with_workspace(_workspace_root()), timeout=30)
    _emit(result, json_output, format_clean)


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
