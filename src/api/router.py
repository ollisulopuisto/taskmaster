"""FastAPI routes for the triage endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from services.gcal_service import GCalService
from services.llm_service import LLMService
from services.todoist_service import TodoistService

router = APIRouter(prefix="/api/triage", tags=["triage"])


class EveningRequest(BaseModel):
    completed_ids: list[str] = []
    rolled_over_ids: list[str] = []


class EveningResponse(BaseModel):
    completed_count: int
    rolled_over_count: int


def _build_dependencies() -> dict[str, Any]:
    """Construct service instances. Separated for testability."""
    return {
        "todoist": TodoistService(token="TODOIST_API_TOKEN"),
        "gcal": GCalService(calendar_ids=["primary"]),
        "llm": LLMService(model="llama3", base_url="http://localhost:11434/v1"),
    }


@router.get("/morning")
def morning_triage() -> dict[str, Any]:
    """Return the LLM's proposed 1-3-5 plan for today."""
    deps = _build_dependencies()
    tasks = deps["todoist"].get_todays_tasks()
    _ = deps["gcal"].get_todays_events()
    free_blocks = deps["gcal"].get_free_time_blocks(
        day_start=datetime.now(tz=UTC).replace(hour=8, minute=0, second=0),
        day_end=datetime.now(tz=UTC).replace(hour=18, minute=0, second=0),
    )
    plan: Any = deps["llm"].plan_triage(tasks=tasks, free_blocks=free_blocks)
    return plan.model_dump()


@router.post("/evening", response_model=EveningResponse)
def evening_debrief(payload: EveningRequest) -> EveningResponse:
    """Record completed and rolled-over tasks."""
    deps = _build_dependencies()
    for task_id in payload.completed_ids:
        deps["todoist"].complete_task(task_id)
    return EveningResponse(
        completed_count=len(payload.completed_ids),
        rolled_over_count=len(payload.rolled_over_ids),
    )


def create_app() -> FastAPI:
    app = FastAPI(title="TaskMaster Triage Helper")
    app.include_router(router)
    return app
