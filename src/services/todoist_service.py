"""Todoist ingestion service.

Fetches tasks that are due today or overdue and normalizes them into the
internal `Task` model used by the rest of the app.
"""

from __future__ import annotations

from datetime import date, datetime

from todoist_api_python.api import TodoistAPI

from models.task import Task as InternalTask


class TodoistService:
    """Wraps the Todoist API and returns normalized internal tasks."""

    def __init__(self, token: str) -> None:
        self._api = TodoistAPI(token)

    def complete_task(self, task_id: str) -> None:
        """Mark a Todoist task as completed."""
        self._api.complete_task(task_id)

    def postpone_task(self, task_id: str, new_date: date) -> None:
        """Push a task's due date to `new_date` (e.g. tomorrow for roll-overs)."""
        self._api.update_task(task_id=task_id, due_date=new_date.isoformat())

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

                due = self._parse_date(raw.due.date)
                if due is None or due > reference:
                    continue

                normalized.append(
                    InternalTask(
                        id=raw.id,
                        content=raw.content,
                        project_id=raw.project_id,
                        due_date=due,
                        labels=list(raw.labels) if raw.labels else [],
                        priority=raw.priority or 1,
                    )
                )
        return normalized

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
