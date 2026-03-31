from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.bootstrap import bootstrap_services
from app.graph.supervisor import Supervisor, SupervisorDeps
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

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/version")
    def version():
        return {"version": __version__}

    @app.post("/teams/create")
    def create_team(payload: TeamCreateRequest):
        services = services_for(payload.workspace_root)
        team = services.create_team(payload.team_id, payload.worker_1_role, payload.worker_2_role)
        start_result = services.start_team(payload.team_id)
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
