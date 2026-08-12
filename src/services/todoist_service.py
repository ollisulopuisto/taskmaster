"""Todoist ingestion service.

Fetches tasks that are due today or overdue and normalizes them into the
internal `Task` model used by the rest of the app.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import date, datetime

from todoist_api_python.api import TodoistAPI

from models.task import Task as InternalTask
from models.task import TriagePlan

DEFAULT_LOCK_LABELS = {"lock", "locked", "taskmaster-lock"}


class TodoistService:
    """Wraps the Todoist API and returns normalized internal tasks."""

    def __init__(self, token: str, lock_labels: Iterable[str] | None = None) -> None:
        self._api = TodoistAPI(token)
        if lock_labels is not None:
            self._lock_labels = {lbl.strip().lower() for lbl in lock_labels if lbl.strip()}
        else:
            env_labels = os.getenv("TODOIST_LOCK_LABELS")
            if env_labels is not None:
                self._lock_labels = {
                    lbl.strip().lower() for lbl in env_labels.split(",") if lbl.strip()
                }
            else:
                self._lock_labels = set(DEFAULT_LOCK_LABELS)

    def complete_task(self, task_id: str) -> None:
        """Mark a Todoist task as completed."""
        self._api.complete_task(task_id)

    def postpone_task(self, task_id: str, new_date: date | str) -> None:
        """Push a task's due date to `new_date` (e.g. tomorrow for roll-overs)."""
        target_date = date.fromisoformat(new_date) if isinstance(new_date, str) else new_date
        self._api.update_task(task_id=task_id, due_date=target_date)

    def get_todays_tasks(self, *, today: date | None = None) -> list[InternalTask]:
        """Return incomplete tasks due today or overdue.

        `today` is injectable for deterministic tests.
        """
        reference = today or date.today()
        paginator = self._api.get_tasks()

        normalized: list[InternalTask] = []
        for page in paginator:
            for raw in page:
                if raw.completed_at is not None:
                    continue
                if raw.due is None or raw.due.date is None:
                    continue
                if getattr(raw.due, "is_recurring", False):
                    continue

                raw_labels = list(raw.labels) if raw.labels else []
                task_labels = {lbl.lower() for lbl in raw_labels}
                if any(lbl in self._lock_labels for lbl in task_labels):
                    continue

                due = self._parse_date(raw.due.date)
                if due is None or due > reference:
                    continue

                is_overdue = due < reference
                days_overdue = (reference - due).days if is_overdue else 0

                duration_minutes = None
                dur = getattr(raw, "duration", None)
                if dur is not None:
                    amt = getattr(dur, "amount", None)
                    unit = getattr(dur, "unit", "minute")
                    if amt is not None:
                        duration_minutes = amt * 60 if unit == "hour" else amt

                normalized.append(
                    InternalTask(
                        id=raw.id,
                        content=raw.content,
                        project_id=raw.project_id,
                        due_date=due,
                        labels=list(raw.labels) if raw.labels else [],
                        priority=raw.priority or 1,
                        duration_minutes=duration_minutes,
                        is_overdue=is_overdue,
                        days_overdue=days_overdue,
                    )
                )
        return normalized

    def sync_plan_priorities(self, plan: TriagePlan, tomorrow: date) -> None:
        """Sync accepted 1-3-5 plan priorities and postponed tasks back to Todoist.

        BIG -> P1 (priority=4)
        MEDIUM -> P2 (priority=3)
        SMALL -> P3 (priority=2)
        POSTPONED -> postponed to tomorrow
        """
        for t in plan.big:
            self._api.update_task(task_id=t.id, priority=4)
        for t in plan.medium:
            self._api.update_task(task_id=t.id, priority=3)
        for t in plan.small:
            self._api.update_task(task_id=t.id, priority=2)
        for t in plan.postponed:
            self.postpone_task(task_id=t.id, new_date=tomorrow)

    @staticmethod
    def _parse_date(value: str | date | datetime) -> date | None:
        """Parse a Todoist date value into a date.

        The SDK may return either a plain ``datetime.date`` / ``datetime.datetime``
        object (newer versions) or an ISO-8601 string (older versions / mocked tests).
        """
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            if "T" in value:
                return datetime.fromisoformat(value).date()
            return date.fromisoformat(value)
        except ValueError:
            return None
