from __future__ import annotations

from fastapi import FastAPI, HTTPException

from app.bootstrap import bootstrap_services
from app.enums import ReviewStatus
from app.graph.supervisor import Supervisor, SupervisorDeps


def create_app() -> FastAPI:
    services = bootstrap_services()
    app = FastAPI(title="Alvis API", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
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
    def run_events(run_id: str):
        return [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in services.list_events(run_id=run_id)
        ]

    @app.get("/reviews")
    def list_reviews():
        return [
            {
                "review_id": review.review_id,
                "run_id": review.run_id,
                "task_id": review.task_id,
                "agent_id": review.agent_id,
                "status": review.status,
                "summary": review.summary,
            }
            for review in services.list_reviews(ReviewStatus.PENDING)
        ]

    @app.post("/reviews/{review_id}/approve")
    def approve(review_id: str):
        review = services.resolve_review(review_id, approved=True)
        if not review:
            raise HTTPException(status_code=404, detail="review not found")
        state = Supervisor(SupervisorDeps(services=services)).resume(review.run_id)
        return {"review_id": review.review_id, "status": review.status, "run_state": state}

    @app.post("/reviews/{review_id}/reject")
    def reject(review_id: str):
        review = services.resolve_review(review_id, approved=False)
        if not review:
            raise HTTPException(status_code=404, detail="review not found")
        return {"review_id": review.review_id, "status": review.status}

    return app
