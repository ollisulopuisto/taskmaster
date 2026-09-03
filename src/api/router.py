"""FastAPI routes for the triage endpoints."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from config import load_config
from models.task import TriageMode
from services.gcal_service import GCalService
from services.llm_service import LLMService
from services.todoist_service import TodoistService

# Load config from ~/.config/taskmaster/.env and .env
load_config()

router = APIRouter(prefix="/api/triage", tags=["triage"])

# Module-level singleton — built once on first request, reused after that.
# Avoids re-fetching the Google API discovery document on every call.
_deps: dict[str, Any] | None = None


class EveningRequest(BaseModel):
    completed_ids: list[str] = []
    rolled_over_ids: list[str] = []


class EveningResponse(BaseModel):
    completed_count: int
    rolled_over_count: int


def _build_dependencies() -> dict[str, Any]:
    """Construct service instances (called once, cached via _get_dependencies)."""
    calendar_ids = [
        cid.strip() for cid in os.getenv("GOOGLE_CALENDAR_IDS", "primary").split(",") if cid.strip()
    ]
    return {
        "todoist": TodoistService(token=os.getenv("TODOIST_API_TOKEN", "")),
        "gcal": GCalService(
            calendar_ids=calendar_ids,
            credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
        ),
        "llm": LLMService.from_env(),
    }


def _get_dependencies() -> dict[str, Any]:
    """Return cached service instances, building them on the first call.

    Caching avoids re-fetching the Google API discovery document
    (a slow network call) on every request.
    """
    global _deps
    if _deps is None:
        _deps = _build_dependencies()
    return _deps


@router.get("/morning")
def morning_triage(
    mode: TriageMode = "balanced",
    time_block: bool = False,
) -> dict[str, Any]:
    """Return the LLM's proposed 1-3-5 plan plus today's GCal schedule.

    ``mode`` shapes how the LLM picks the tasks; ``time_block`` additionally runs
    pass 2, fitting the selected tasks into today's free calendar blocks.
    """
    deps = _get_dependencies()
    tasks = deps["todoist"].get_todays_tasks()
    events = deps["gcal"].get_todays_events()
    tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
    free_blocks = deps["gcal"].get_free_time_blocks(
        day_start=datetime.now(tz=tz).replace(hour=8, minute=0, second=0),
        day_end=datetime.now(tz=tz).replace(hour=18, minute=0, second=0),
    )
    plan: Any = deps["llm"].plan_triage(
        tasks=tasks,
        free_blocks=free_blocks,
        mode=mode,
        time_block=time_block,
    )
    return {
        "plan": plan.model_dump(),
        "schedule": {
            "events": events,
            "free_blocks": [fb.model_dump() for fb in free_blocks],
        },
    }


@router.get("/debug")
def debug_triage() -> dict[str, Any]:
    """Return raw ingested data from each service — no LLM call.

    Useful for inspecting what Todoist tasks and GCal events were fetched,
    what the LLM prompt looks like, and what model/config is active.
    """
    deps = _get_dependencies()
    tasks = deps["todoist"].get_todays_tasks()
    events = deps["gcal"].get_todays_events()
    tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
    day_start = datetime.now(tz=tz).replace(hour=8, minute=0, second=0)
    day_end = datetime.now(tz=tz).replace(hour=18, minute=0, second=0)
    free_blocks = deps["gcal"].get_free_time_blocks(
        day_start=day_start,
        day_end=day_end,
    )

    from services.llm_service import LLMService  # local import to access _build_prompt

    llm_prompt = LLMService._build_prompt(tasks, free_blocks)

    return {
        "config": {
            "model": os.getenv("OLLAMA_MODEL"),
            "base_url": os.getenv("OLLAMA_BASE_URL"),
            "calendar_ids": os.getenv("GOOGLE_CALENDAR_IDS"),
            "timezone": os.getenv("TIMEZONE"),
            "day_window": f"{day_start.isoformat()} → {day_end.isoformat()}",
        },
        "todoist": {
            "task_count": len(tasks),
            "tasks": [t.model_dump() for t in tasks],
        },
        "gcal": {
            "event_count": len(events),
            "events": [
                {
                    "summary": e.get("summary", "(no title)"),
                    "start": e.get("start", {}),
                    "end": e.get("end", {}),
                    "status": e.get("status"),
                }
                for e in events
            ],
            "free_block_count": len(free_blocks),
            "free_blocks": [fb.model_dump() for fb in free_blocks],
        },
        "llm_prompt": llm_prompt,
    }


@router.post("/evening", response_model=EveningResponse)
def evening_debrief(payload: EveningRequest) -> EveningResponse:
    """Record completed tasks and roll incomplete ones over to tomorrow."""
    deps = _get_dependencies()
    todoist = deps["todoist"]

    for task_id in payload.completed_ids:
        todoist.complete_task(task_id)

    tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
    tomorrow = (datetime.now(tz=tz) + timedelta(days=1)).date()
    for task_id in payload.rolled_over_ids:
        todoist.postpone_task(task_id, tomorrow)

    return EveningResponse(
        completed_count=len(payload.completed_ids),
        rolled_over_count=len(payload.rolled_over_ids),
    )


def create_app() -> FastAPI:
    app = FastAPI(title="TaskMaster Triage Helper")
    app.include_router(router)
    return app
