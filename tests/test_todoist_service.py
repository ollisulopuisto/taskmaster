"""Tests for the Todoist service."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from models.task import Task as InternalTask
from services.todoist_service import TodoistService


def _make_todoist_task(
    *,
    id: str = "1",
    content: str = "Test task",
    project_id: str = "proj-1",
    due_date: str | None = "2026-06-28",
    is_completed: bool = False,
    is_recurring: bool = False,
    labels: list[str] | None = None,
) -> MagicMock:
    """Build a fake todoist_api_python Task with the attributes we read."""
    from todoist_api_python.models import Due, Task

    due = None
    if due_date is not None:
        due = Due(
            date=due_date, string=due_date, lang="en", is_recurring=is_recurring, timezone=None
        )

    return Task(
        id=id,
        content=content,
        description="",
        project_id=project_id,
        section_id=None,
        parent_id=None,
        labels=labels,
        priority=1,
        due=due,
        deadline=None,
        duration=None,
        is_collapsed=False,
        order=0,
        assignee_id=None,
        assigner_id=None,
        completed_at=None,
        creator_id="u1",
        created_at=datetime(2026, 6, 27, tzinfo=UTC),
        updated_at=datetime(2026, 6, 27, tzinfo=UTC),
        meta=None,
    )


class TestTodoistService:
    def _service(self) -> TodoistService:
        return TodoistService(token="fake-token")

    def test_get_todays_tasks_returns_standardized_models(self) -> None:
        fake_task = _make_todoist_task(id="1", content="Write report", due_date="2026-06-28")

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[fake_task]])

            result = self._service().get_todays_tasks()

        assert len(result) == 1
        task = result[0]
        assert isinstance(task, InternalTask)
        assert task.id == "1"
        assert task.content == "Write report"
        assert task.project_id == "proj-1"
        assert task.due_date == date(2026, 6, 28)

    def test_get_todays_tasks_filters_out_future_tasks(self) -> None:
        today = _make_todoist_task(id="1", content="Today", due_date="2026-06-28")
        future = _make_todoist_task(id="2", content="Next week", due_date="2026-07-05")

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[today, future]])

            result = self._service().get_todays_tasks(today=date(2026, 6, 28))

        assert [t.id for t in result] == ["1"]

    def test_get_todays_tasks_keeps_overdue_tasks(self) -> None:
        overdue = _make_todoist_task(id="1", content="Overdue", due_date="2026-06-25")

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[overdue]])

            result = self._service().get_todays_tasks()

        assert [t.id for t in result] == ["1"]

    def test_get_todays_tasks_excludes_completed(self) -> None:
        completed = _make_todoist_task(id="1", content="Done", due_date="2026-06-28")
        # The library exposes is_completed as a property; emulate via completed_at.
        completed.completed_at = datetime(2026, 6, 27, tzinfo=UTC)

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[completed]])

            result = self._service().get_todays_tasks()

        assert result == []

    def test_get_todays_tasks_handles_no_due_date(self) -> None:
        no_due = _make_todoist_task(id="1", content="No due", due_date=None)

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[no_due]])

            result = self._service().get_todays_tasks()

        # Tasks without a due date are not "today or overdue" — excluded.
        assert result == []

    def test_get_todays_tasks_excludes_recurring_tasks(self) -> None:
        recurring = _make_todoist_task(
            id="1", content="Daily Standup", due_date="2026-06-28", is_recurring=True
        )
        one_off = _make_todoist_task(
            id="2", content="One-off Task", due_date="2026-06-28", is_recurring=False
        )

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[recurring, one_off]])

            result = self._service().get_todays_tasks()

        assert [t.id for t in result] == ["2"]

    def test_get_todays_tasks_paginates(self) -> None:
        page1 = [_make_todoist_task(id="1", content="A", due_date="2026-06-28")]
        page2 = [_make_todoist_task(id="2", content="B", due_date="2026-06-28")]

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([page1, page2])

            result = self._service().get_todays_tasks()

        assert [t.id for t in result] == ["1", "2"]

    def test_get_todays_tasks_excludes_locked_tasks_default_labels(self) -> None:
        normal = _make_todoist_task(id="1", content="Normal task", labels=["work"])
        locked1 = _make_todoist_task(id="2", content="Locked 1", labels=["locked"])
        locked2 = _make_todoist_task(id="3", content="Locked 2", labels=["Lock"])
        locked3 = _make_todoist_task(id="4", content="Locked 3", labels=["taskmaster-lock"])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[normal, locked1, locked2, locked3]])

            result = self._service().get_todays_tasks()

        assert [t.id for t in result] == ["1"]

    def test_get_todays_tasks_locked_labels_override_via_env(self) -> None:
        normal = _make_todoist_task(id="1", content="Normal task", labels=["locked"])
        locked = _make_todoist_task(id="2", content="Locked task", labels=["custom-lock"])

        with (
            patch("services.todoist_service.TodoistAPI") as MockAPI,
            patch.dict("os.environ", {"TODOIST_LOCK_LABELS": "custom-lock, private-lock"}),
        ):
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[normal, locked]])

            result = self._service().get_todays_tasks()

        assert [t.id for t in result] == ["1"]

    def test_get_todays_tasks_locked_labels_override_via_init(self) -> None:
        normal = _make_todoist_task(id="1", content="Normal task", labels=["locked"])
        locked = _make_todoist_task(id="2", content="Locked task", labels=["do-not-triage"])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_tasks.return_value = iter([[normal, locked]])

            svc = TodoistService(token="fake-token", lock_labels=["do-not-triage"])
            result = svc.get_todays_tasks()

        assert [t.id for t in result] == ["1"]

    def test_postpone_task_updates_due_date(self) -> None:
        from datetime import date

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.postpone_task("1", date(2026, 6, 29))

        MockAPI.return_value.update_task.assert_called_once_with(
            task_id="1", due_date=date(2026, 6, 29)
        )

    def test_postpone_task_accepts_string_id(self) -> None:
        from datetime import date

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.postpone_task("abc-123", date(2026, 7, 1))

        MockAPI.return_value.update_task.assert_called_once_with(
            task_id="abc-123", due_date=date(2026, 7, 1)
        )

    def test_sync_plan_tags_updates_todoist(self) -> None:
        from models.task import TriagePlan

        t_big = InternalTask(id="b1", content="Big", project_id="p1", labels=["work"])
        t_med = InternalTask(id="m1", content="Med", project_id="p1", labels=["small"])
        t_small = InternalTask(id="s1", content="Small", project_id="p1")
        t_post = InternalTask(
            id="p1", content="Postponed", project_id="p1", labels=["medium", "personal"]
        )

        plan = TriagePlan(big=[t_big], medium=[t_med], small=[t_small], postponed=[t_post])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.sync_plan_tags(plan, tomorrow=date(2026, 6, 29))

            inst = MockAPI.return_value
            inst.update_task.assert_any_call(task_id="b1", labels=["work", "big"])
            inst.update_task.assert_any_call(task_id="m1", labels=["medium"])
            inst.update_task.assert_any_call(task_id="s1", labels=["small"])
            inst.update_task.assert_any_call(
                task_id="p1", due_date=date(2026, 6, 29), labels=["personal"]
            )

    def test_validate_credentials_valid(self) -> None:
        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            inst = MockAPI.return_value
            inst.get_projects.return_value = []
            svc = TodoistService(token="valid-token")
            ok, msg = svc.validate_credentials()
            assert ok is True
            assert "valid" in msg.lower()

    def test_validate_credentials_missing_token(self) -> None:
        svc = TodoistService(token="")
        ok, msg = svc.validate_credentials()
        assert ok is False
        assert "missing" in msg.lower()

    def test_validate_credentials_api_error(self) -> None:
        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            inst = MockAPI.return_value
            inst.get_projects.side_effect = Exception("401 Unauthorized")
            svc = TodoistService(token="bad-token")
            ok, msg = svc.validate_credentials()
            assert ok is False
            assert "401 Unauthorized" in msg
