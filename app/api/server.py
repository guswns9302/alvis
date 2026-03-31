from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from app.bootstrap import bootstrap_services
from app.graph.supervisor import Supervisor, SupervisorDeps
from app.sessions.tmux_manager import TmuxUnavailableError
from app.version import __version__


class TeamCreateRequest(BaseModel):
    workspace_root: str | None = None
    team_id: str
    worker_1_role: str
    worker_2_role: str


class TeamRequest(BaseModel):
    workspace_root: str | None = None
    team_id: str


class RunRequest(BaseModel):
    workspace_root: str | None = None
    team_id: str
    request: str


class ResumeRequest(BaseModel):
    workspace_root: str | None = None


class RecoverRequest(BaseModel):
    workspace_root: str | None = None
    team_id: str | None = None
    retry: bool = False


def create_app() -> FastAPI:
    app = FastAPI(title="Alvis API", version=__version__)

    def services_for(workspace_root: str | None = None):
        return bootstrap_services(workspace_root)

    @app.exception_handler(TmuxUnavailableError)
    async def tmux_unavailable_handler(_: Request, exc: TmuxUnavailableError):
        return JSONResponse(
            status_code=503,
            content={
                "error_code": "tmux_unavailable",
                "detail": str(exc),
                "hint": "tmux 경로를 daemon이 찾지 못했습니다. `alvis doctor`와 `alvis daemon restart`를 실행하거나 install.sh를 다시 실행하세요.",
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError):
        message = str(exc)
        error_code = "invalid_request"
        status_code = 400
        hint = None
        if "already exists" in message:
            error_code = "team_exists"
            status_code = 409
            hint = "다른 팀 이름을 사용하거나 `alvis team remove <team_id>` 후 다시 시도하세요."
        elif "not found" in message:
            error_code = "not_found"
            status_code = 404
        return JSONResponse(
            status_code=status_code,
            content={
                "error_code": error_code,
                "detail": message,
                "hint": hint,
            },
        )

    @app.get("/health")
    def health(workspace_root: str | None = None):
        services = services_for(workspace_root)
        diagnostics = services.daemon_health()
        return {
            "status": "ok",
            "version": __version__,
            "daemon_tmux_path": diagnostics["tmux_path"],
            "daemon_tmux_available": diagnostics["tmux_available"],
            "daemon_codex_command": diagnostics["codex_command"],
            "daemon_workspace_root": diagnostics["workspace_root"],
            "daemon_data_dir": diagnostics["data_dir"],
        }

    @app.get("/version")
    def version():
        return {"version": __version__}

    @app.post("/teams/create")
    def create_team(payload: TeamCreateRequest):
        services = services_for(payload.workspace_root)
        provisioned = services.provision_team(payload.team_id, payload.worker_1_role, payload.worker_2_role)
        team = provisioned["team"]
        start_result = provisioned["start_result"]
        return {
            "team_id": team.team_id,
            "workers": [
                {
                    "agent_id": f"{payload.team_id}-worker-1",
                    "role": payload.worker_1_role.split(":", 1)[0],
                    "role_alias": payload.worker_1_role.split(":", 1)[1] if ":" in payload.worker_1_role else payload.worker_1_role,
                },
                {
                    "agent_id": f"{payload.team_id}-worker-2",
                    "role": payload.worker_2_role.split(":", 1)[0],
                    "role_alias": payload.worker_2_role.split(":", 1)[1] if ":" in payload.worker_2_role else payload.worker_2_role,
                },
            ],
            "start_result": start_result,
        }

    @app.post("/teams/start")
    def start_team(payload: TeamRequest):
        services = services_for(payload.workspace_root)
        return services.start_team(payload.team_id)

    @app.post("/teams/remove")
    def remove_team(payload: TeamRequest):
        services = services_for(payload.workspace_root)
        return services.remove_team(payload.team_id)

    @app.post("/runs")
    def run(payload: RunRequest):
        services = services_for(payload.workspace_root)
        supervisor = Supervisor(SupervisorDeps(services=services))
        return supervisor.run(payload.team_id, payload.request)

    @app.post("/runs/{run_id}/resume")
    def resume(run_id: str, payload: ResumeRequest):
        services = services_for(payload.workspace_root)
        supervisor = Supervisor(SupervisorDeps(services=services))
        return supervisor.resume(run_id)

    @app.get("/status")
    def status(team_id: str, workspace_root: str | None = None):
        services = services_for(workspace_root)
        try:
            return services.status(team_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/logs")
    def logs(team_id: str, run_id: str | None = None, workspace_root: str | None = None):
        services = services_for(workspace_root)
        return [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "agent_id": event.agent_id,
                "task_id": event.task_id,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in services.list_events(team_id=team_id, run_id=run_id)
        ]

    @app.post("/recover")
    def recover(payload: RecoverRequest):
        services = services_for(payload.workspace_root)
        return services.recover(team_id=payload.team_id, retry=payload.retry)

    @app.post("/cleanup")
    def cleanup(payload: RecoverRequest):
        services = services_for(payload.workspace_root)
        return services.cleanup_worktrees(team_id=payload.team_id)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, workspace_root: str | None = None):
        services = services_for(workspace_root)
        run = services.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "run_id": run.run_id,
            "team_id": run.team_id,
            "request": run.request,
            "status": run.status,
            "final_response": run.final_response,
        }

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, workspace_root: str | None = None):
        services = services_for(workspace_root)
        return [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in services.list_events(run_id=run_id)
        ]

    return app
