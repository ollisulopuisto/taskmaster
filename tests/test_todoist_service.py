"""Tests for the Todoist service."""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest

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

    def test_get_todays_tasks_concurrent_fetching(self) -> None:
        """Verify get_todays_tasks fetches project names and tasks concurrently."""
        thread_ids: set[int] = set()
        lock = threading.Lock()

        def slow_get_projects() -> list[object]:
            with lock:
                thread_ids.add(threading.get_ident())
            time.sleep(0.05)
            proj = MagicMock()
            proj.id = "proj-1"
            proj.name = "Work"
            return [proj]

        def slow_get_tasks() -> object:
            with lock:
                thread_ids.add(threading.get_ident())
            time.sleep(0.05)
            task = _make_todoist_task(
                id="1", content="Task 1", project_id="proj-1", due_date="2026-06-28"
            )
            return iter([[task]])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            instance = MockAPI.return_value
            instance.get_projects.side_effect = slow_get_projects
            instance.get_tasks.side_effect = slow_get_tasks

            t0 = time.perf_counter()
            result = self._service().get_todays_tasks()
            elapsed = time.perf_counter() - t0

            # 2 calls * 0.05s sequential = 0.10s; concurrent execution finishes well under 0.09s
            assert elapsed < 0.09, (
                f"get_todays_tasks was too slow ({elapsed:.3f}s), not running concurrently"
            )
            assert len(thread_ids) > 1, f"Expected multiple worker threads, got {len(thread_ids)}"
            assert len(result) == 1
            assert result[0].project_name == "Work"

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

    def test_postpone_tasks_concurrent_execution(self) -> None:
        """Verify postpone_tasks executes task postponement concurrently across worker threads."""
        import threading
        import time
        from datetime import date

        thread_ids: set[int] = set()
        lock = threading.Lock()

        def slow_update_task(**kwargs: object) -> None:
            with lock:
                thread_ids.add(threading.get_ident())
            time.sleep(0.04)

        task_ids = [f"task_{i}" for i in range(8)]
        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            MockAPI.return_value.update_task.side_effect = slow_update_task

            t0 = time.perf_counter()
            svc.postpone_tasks(task_ids, date(2026, 6, 29))
            elapsed = time.perf_counter() - t0

            # 8 tasks * 0.04s sequential = 0.32s; concurrent execution completes under 0.20s
            assert elapsed < 0.20, (
                f"postpone_tasks was too slow ({elapsed:.3f}s), not running concurrently"
            )
            assert len(thread_ids) > 1, f"Expected multiple worker threads, got {len(thread_ids)}"
            assert MockAPI.return_value.update_task.call_count == 8

    def test_postpone_tasks_empty_list_noop(self) -> None:
        from datetime import date

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.postpone_tasks([], date(2026, 6, 29))
            MockAPI.return_value.update_task.assert_not_called()

    def test_complete_tasks_executes_api_complete_task(self) -> None:
        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.complete_tasks(["task_1", "task_2"])

            inst = MockAPI.return_value
            inst.complete_task.assert_any_call(task_id="task_1")
            inst.complete_task.assert_any_call(task_id="task_2")
            assert inst.complete_task.call_count == 2

    def test_complete_tasks_concurrent_execution(self) -> None:
        """Verify complete_tasks executes task completion concurrently across worker threads."""
        import threading
        import time

        thread_ids: set[int] = set()
        lock = threading.Lock()

        def slow_complete_task(task_id: str) -> None:
            with lock:
                thread_ids.add(threading.get_ident())
            time.sleep(0.04)

        task_ids = [f"task_{i}" for i in range(8)]
        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            MockAPI.return_value.complete_task.side_effect = slow_complete_task

            t0 = time.perf_counter()
            svc.complete_tasks(task_ids)
            elapsed = time.perf_counter() - t0

            # 8 tasks * 0.04s sequential = 0.32s; concurrent execution completes under 0.20s
            assert elapsed < 0.20, (
                f"complete_tasks was too slow ({elapsed:.3f}s), not running concurrently"
            )
            assert len(thread_ids) > 1, f"Expected multiple worker threads, got {len(thread_ids)}"
            assert MockAPI.return_value.complete_task.call_count == 8

    def test_complete_tasks_empty_list_noop(self) -> None:
        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.complete_tasks([])
            MockAPI.return_value.complete_task.assert_not_called()

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

    def test_sync_plan_tags_parallel_execution(self) -> None:
        """Verify sync_plan_tags executes updates concurrently across multiple worker threads."""
        import threading
        import time

        from models.task import TriagePlan

        thread_ids: set[int] = set()
        lock = threading.Lock()

        def slow_update_task(**kwargs: object) -> None:
            with lock:
                thread_ids.add(threading.get_ident())
            time.sleep(0.04)

        tasks = [
            InternalTask(id=f"t{i}", content=f"Task {i}", project_id="p1", labels=[])
            for i in range(8)
        ]
        plan = TriagePlan(
            big=[tasks[0]],
            medium=tasks[1:4],
            small=tasks[4:],
            postponed=[],
        )

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            MockAPI.return_value.update_task.side_effect = slow_update_task

            t0 = time.perf_counter()
            svc.sync_plan_tags(plan, tomorrow=date(2026, 6, 29))
            elapsed = time.perf_counter() - t0

            # 8 tasks * 0.04s sequential = 0.32s; concurrent execution completes under 0.20s
            assert elapsed < 0.20, (
                f"sync_plan_tags was too slow ({elapsed:.3f}s), not running concurrently"
            )
            assert len(thread_ids) > 1, f"Expected multiple worker threads, got {len(thread_ids)}"
            assert MockAPI.return_value.update_task.call_count == 8

    def test_sync_plan_tags_propagates_exceptions(self) -> None:
        """Verify errors during concurrent task updates are raised."""
        from models.task import TriagePlan

        tasks = [InternalTask(id="err1", content="Task", project_id="p1", labels=[])]
        plan = TriagePlan(big=[tasks[0]])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            MockAPI.return_value.update_task.side_effect = RuntimeError("API Rate Limited")

            with pytest.raises(RuntimeError, match="API Rate Limited"):
                svc.sync_plan_tags(plan, tomorrow=date(2026, 6, 29))

    def test_sync_plan_tags_empty_plan_noop(self) -> None:
        """Verify sync_plan_tags on an empty plan does not trigger update_task calls."""
        from models.task import TriagePlan

        plan = TriagePlan()
        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.sync_plan_tags(plan, tomorrow=date(2026, 6, 29))
            MockAPI.return_value.update_task.assert_not_called()

    def test_sync_plan_tags_skips_redundant_updates_when_already_synced(self) -> None:
        """Verify sync_plan_tags skips remote update_task calls if tasks already match."""
        from models.task import TriagePlan

        tomorrow = date(2026, 6, 29)
        t_big = InternalTask(id="b1", content="Big", project_id="p1", labels=["work", "big"])
        t_med = InternalTask(id="m1", content="Med", project_id="p1", labels=["medium"])
        t_small = InternalTask(id="s1", content="Small", project_id="p1", labels=["small"])
        t_post = InternalTask(
            id="p1", content="Postponed", project_id="p1", labels=["personal"], due_date=tomorrow
        )

        plan = TriagePlan(big=[t_big], medium=[t_med], small=[t_small], postponed=[t_post])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.sync_plan_tags(plan, tomorrow=tomorrow)

            # All tasks already have correct tags and due dates; 0 updates should be dispatched
            MockAPI.return_value.update_task.assert_not_called()

    def test_sync_plan_tags_only_updates_changed_tasks(self) -> None:
        """Verify sync_plan_tags only dispatches update_task for tasks that actually changed."""
        from models.task import TriagePlan

        tomorrow = date(2026, 6, 29)
        # b1 already has 'big' tag; b2 lacks 'big' tag
        t_big_unchanged = InternalTask(
            id="b1", content="Big 1", project_id="p1", labels=["work", "big"]
        )
        t_big_changed = InternalTask(id="b2", content="Big 2", project_id="p1", labels=["work"])
        # p1 already has tomorrow's date and no size labels; p2 has old date
        t_post_unchanged = InternalTask(
            id="p1", content="Post 1", project_id="p1", labels=[], due_date=tomorrow
        )
        t_post_changed = InternalTask(
            id="p2", content="Post 2", project_id="p1", labels=["big"], due_date=date(2026, 6, 28)
        )

        plan = TriagePlan(
            big=[t_big_unchanged, t_big_changed],
            medium=[],
            small=[],
            postponed=[t_post_unchanged, t_post_changed],
        )

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            svc.sync_plan_tags(plan, tomorrow=tomorrow)

            inst = MockAPI.return_value
            assert inst.update_task.call_count == 2
            inst.update_task.assert_any_call(task_id="b2", labels=["work", "big"])
            inst.update_task.assert_any_call(task_id="p2", labels=[], due_date=tomorrow)

    def test_sync_plan_tags_uses_fresh_remote_state_for_skip_check(self) -> None:
        """Regression (Devin on #31): a tag changed in Todoist after ingestion
        must not escape the sync even when the snapshot already matches the plan."""
        from types import SimpleNamespace

        from models.task import TriagePlan

        tomorrow = date(2026, 6, 29)
        # Snapshot says the task already has "big", but the user removed it remotely.
        t_big = InternalTask(id="b1", content="Big", project_id="p1", labels=["work", "big"])
        plan = TriagePlan(big=[t_big], medium=[], small=[], postponed=[])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            inst = MockAPI.return_value
            inst.get_task.return_value = SimpleNamespace(labels=["work"], due=None)

            svc.sync_plan_tags(plan, tomorrow=tomorrow)

            inst.update_task.assert_called_once_with(task_id="b1", labels=["work", "big"])

    def test_sync_plan_tags_skips_when_fresh_state_matches(self) -> None:
        """When the freshly fetched remote state already matches, no update is sent."""
        from types import SimpleNamespace

        from models.task import TriagePlan

        tomorrow = date(2026, 6, 29)
        t_big = InternalTask(id="b1", content="Big", project_id="p1", labels=["work"])
        plan = TriagePlan(big=[t_big], medium=[], small=[], postponed=[])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            inst = MockAPI.return_value
            inst.get_task.return_value = SimpleNamespace(labels=["work", "big"], due=None)

            svc.sync_plan_tags(plan, tomorrow=tomorrow)

            inst.update_task.assert_not_called()

    def test_sync_plan_tags_falls_back_to_snapshot_when_fetch_fails(self) -> None:
        """A failed get_task falls back to the snapshot, preserving prior behavior."""
        from models.task import TriagePlan

        tomorrow = date(2026, 6, 29)
        t_big = InternalTask(id="b1", content="Big", project_id="p1", labels=["work"])
        plan = TriagePlan(big=[t_big], medium=[], small=[], postponed=[])

        with patch("services.todoist_service.TodoistAPI") as MockAPI:
            svc = self._service()
            inst = MockAPI.return_value
            inst.get_task.side_effect = RuntimeError("network down")

            svc.sync_plan_tags(plan, tomorrow=tomorrow)

            inst.update_task.assert_called_once_with(task_id="b1", labels=["work", "big"])

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


class TestProjectNameEnrichment:
    """`Task.project_name` feeds the LLM prompt, so it must actually be populated."""

    def _service(self) -> TodoistService:
        return TodoistService(token="fake-token")

    @staticmethod
    def _project(id: str, name: str) -> MagicMock:
        proj = MagicMock()
        proj.id = id
        proj.name = name
        return proj

    def test_project_name_is_resolved_from_the_projects_api(self) -> None:
        fake_task = _make_todoist_task(id="1", project_id="proj-1", due_date="2026-06-28")
        with patch("services.todoist_service.TodoistAPI") as mock_api:
            instance = mock_api.return_value
            instance.get_tasks.return_value = iter([[fake_task]])
            instance.get_projects.return_value = iter([[self._project("proj-1", "Backend")]])

            tasks = self._service().get_todays_tasks(today=date(2026, 6, 28))

        assert tasks[0].project_name == "Backend"

    def test_flat_project_list_is_also_supported(self) -> None:
        fake_task = _make_todoist_task(id="1", project_id="proj-1", due_date="2026-06-28")
        with patch("services.todoist_service.TodoistAPI") as mock_api:
            instance = mock_api.return_value
            instance.get_tasks.return_value = iter([[fake_task]])
            instance.get_projects.return_value = [self._project("proj-1", "Backend")]

            tasks = self._service().get_todays_tasks(today=date(2026, 6, 28))

        assert tasks[0].project_name == "Backend"

    def test_project_lookup_failure_does_not_break_ingestion(self) -> None:
        fake_task = _make_todoist_task(id="1", project_id="proj-1", due_date="2026-06-28")
        with patch("services.todoist_service.TodoistAPI") as mock_api:
            instance = mock_api.return_value
            instance.get_tasks.return_value = iter([[fake_task]])
            instance.get_projects.side_effect = Exception("500 Server Error")

            tasks = self._service().get_todays_tasks(today=date(2026, 6, 28))

        assert len(tasks) == 1
        assert tasks[0].project_name is None

    def test_projects_are_fetched_once_per_ingest(self) -> None:
        tasks_in = [
            _make_todoist_task(id="1", project_id="proj-1", due_date="2026-06-28"),
            _make_todoist_task(id="2", project_id="proj-1", due_date="2026-06-28"),
        ]
        with patch("services.todoist_service.TodoistAPI") as mock_api:
            instance = mock_api.return_value
            instance.get_tasks.return_value = iter([tasks_in])
            instance.get_projects.return_value = iter([[self._project("proj-1", "Backend")]])

            self._service().get_todays_tasks(today=date(2026, 6, 28))

        assert instance.get_projects.call_count == 1
