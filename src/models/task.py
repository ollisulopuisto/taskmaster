"""Internal Pydantic models shared across services and the LLM brain."""

from __future__ import annotations

from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel

Domain = Literal["work", "civilian", "family"]
Quadrant = Literal["urgent_important", "not_urgent_important", "urgent_not_important", "neither"]
TriageMode = Literal["balanced", "deep_work", "admin", "low_energy"]
# Single source of truth for UIs that need to enumerate the modes (argparse, TUI).
TRIAGE_MODES: tuple[str, ...] = get_args(TriageMode)


class Task(BaseModel):
    """A normalized task coming from any source (Todoist, etc.)."""

    id: str
    content: str
    project_id: str
    project_name: str | None = None
    due_date: date | None = None
    labels: list[str] = []
    priority: int = 1
    duration_minutes: int | None = None
    is_overdue: bool = False
    days_overdue: int = 0

    @property
    def is_stale(self) -> bool:
        """Tasks overdue by 7 or more days are considered stale."""
        return self.days_overdue >= 7


class TimeBlock(BaseModel):
    """A free-time window derived from Google Calendar."""

    start: str  # ISO-8601 datetime
    end: str  # ISO-8601 duration (e.g. "PT1H") or end datetime


class ScheduledSlot(BaseModel):
    """A task time-blocked into a specific calendar interval."""

    task_id: str
    task_content: str
    start_time: str
    end_time: str
    category: Literal["big", "medium", "small"] = "medium"
    notes: str | None = None


class DailySchedule(BaseModel):
    """Structured LLM output for Pass 2 time-blocking."""

    slots: list[ScheduledSlot] = []
    total_planned_minutes: int = 0
    is_overcapacity: bool = False
    summary: str = ""


class LLMBackendConfig(BaseModel):
    """Configuration for an individual LLM backend."""

    key: str
    name: str
    base_url: str
    model: str
    api_key: str = "ollama"
    timeout: float = 600.0


class TriagePlanIDs(BaseModel):
    """Lightweight structured LLM output containing task IDs per category."""

    big: list[str] = []
    medium: list[str] = []
    small: list[str] = []
    postponed: list[str] = []
    quadrant: Quadrant = "not_urgent_important"
    domain: Domain = "civilian"


class TriagePlan(BaseModel):
    """Structured LLM output and interactive user plan."""

    big: list[Task] = []
    medium: list[Task] = []
    small: list[Task] = []
    postponed: list[Task] = []
    quadrant: Quadrant = "not_urgent_important"
    domain: Domain = "civilian"
    mode: TriageMode = "balanced"
    schedule: DailySchedule | None = None

    def reassign_task(
        self, task_id: str, target: Literal["big", "medium", "small", "postponed"]
    ) -> TriagePlan:
        """Reassign a task to a target category, auto-shifting overflow tasks."""
        # Collect all tasks currently across all categories
        all_map = {t.id: t for t in (self.big + self.medium + self.small + self.postponed)}
        if task_id not in all_map:
            return self

        target_task = all_map[task_id]

        # Filter out target_task from current lists
        big_list = [t for t in self.big if t.id != task_id]
        medium_list = [t for t in self.medium if t.id != task_id]
        small_list = [t for t in self.small if t.id != task_id]
        postponed_list = [t for t in self.postponed if t.id != task_id]

        if target == "big":
            # Demote existing big task to medium
            displaced = big_list
            big_list = [target_task]
            medium_list = displaced + medium_list
        elif target == "medium":
            medium_list.insert(0, target_task)
        elif target == "small":
            small_list.insert(0, target_task)
        elif target == "postponed":
            postponed_list.insert(0, target_task)

        # Enforce capacity limits: medium max 3, small max 5
        while len(medium_list) > 3:
            overflow = medium_list.pop()
            small_list.insert(0, overflow)

        while len(small_list) > 5:
            overflow = small_list.pop()
            postponed_list.insert(0, overflow)

        return TriagePlan(
            big=big_list,
            medium=medium_list,
            small=small_list,
            postponed=postponed_list,
            quadrant=self.quadrant,
            domain=self.domain,
        )
