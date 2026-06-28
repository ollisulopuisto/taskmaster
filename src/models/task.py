"""Internal Pydantic models shared across services and the LLM brain."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

Domain = Literal["work", "civilian", "family"]
Quadrant = Literal["urgent_important", "not_urgent_important", "urgent_not_important", "neither"]


class Task(BaseModel):
    """A normalized task coming from any source (Todoist, etc.)."""

    id: str
    content: str
    project_id: str
    due_date: date | None = None
    labels: list[str] = []
    priority: int = 1


class TimeBlock(BaseModel):
    """A free-time window derived from Google Calendar."""

    start: str  # ISO-8601 datetime
    end: str  # ISO-8601 duration (e.g. "PT1H") or end datetime


class TriagePlan(BaseModel):
    """Structured LLM output: the proposed daily plan."""

    big: list[Task] = []
    medium: list[Task] = []
    small: list[Task] = []
    quadrant: Quadrant = "not_urgent_important"
    domain: Domain = "civilian"
