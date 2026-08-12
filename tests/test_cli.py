"""Tests for the CLI / TUI interface."""

from datetime import date
from unittest.mock import MagicMock, patch

from cli import run_cli
from models.task import Task, TimeBlock, TriagePlan


def test_cli_auto_mode(capsys, pytestconfig):
    """Test autonomous (non-interactive) CLI execution."""
    mock_tasks = [
        Task(id="t1", content="Big task", project_id="p1", due_date=date.today(), priority=4),
        Task(id="t2", content="Small task", project_id="p1", due_date=date.today(), priority=1),
    ]
    mock_plan = TriagePlan(
        big=[mock_tasks[0]],
        medium=[],
        small=[mock_tasks[1]],
        quadrant="urgent_important",
        domain="work",
    )
    mock_events = [{"summary": "Team Sync", "start": {"dateTime": "2026-07-22T09:00:00+03:00"}}]
    mock_blocks = [TimeBlock(start="2026-07-22T10:00:00+03:00", end="2026-07-22T12:00:00+03:00")]

    with (
        patch("cli.TodoistService") as mock_todoist_cls,
        patch("cli.GCalService") as mock_gcal_cls,
        patch("cli.LLMService") as mock_llm_cls,
    ):
        mock_todoist = MagicMock()
        mock_todoist.get_todays_tasks.return_value = mock_tasks
        mock_todoist.validate_credentials.return_value = (True, "Valid")
        mock_todoist_cls.return_value = mock_todoist

        mock_gcal = MagicMock()
        mock_gcal.get_todays_events.return_value = mock_events
        mock_gcal.get_free_time_blocks.return_value = mock_blocks
        mock_gcal.validate_credentials.return_value = (True, "Valid")
        mock_gcal_cls.return_value = mock_gcal
        mock_gcal_cls.validate_credentials_static.return_value = (True, "Valid")

        mock_llm = MagicMock()
        mock_llm.plan_triage.return_value = mock_plan
        mock_llm_cls.return_value = mock_llm
        mock_llm_cls.from_env.return_value = mock_llm
        mock_llm_cls.validate_backend.return_value = (True, "Valid")

        plan = run_cli(auto=True)

        assert plan == mock_plan
        captured = capsys.readouterr()
        assert "Autonomous morning triage completed" in captured.out or "Plan" in captured.out


def test_cli_dry_run_flag(capsys):
    """Test CLI dry-run mode prevents Todoist sync even if --sync is requested."""
    mock_tasks = [
        Task(id="t1", content="Big task", project_id="p1", due_date=date.today(), priority=4)
    ]
    mock_plan = TriagePlan(big=mock_tasks, quadrant="urgent_important", domain="work")

    with (
        patch("cli.TodoistService") as mock_todoist_cls,
        patch("cli.GCalService") as mock_gcal_cls,
        patch("cli.LLMService") as mock_llm_cls,
    ):
        mock_todoist = MagicMock()
        mock_todoist.get_todays_tasks.return_value = mock_tasks
        mock_todoist.validate_credentials.return_value = (True, "Valid")
        mock_todoist_cls.return_value = mock_todoist

        mock_gcal = MagicMock()
        mock_gcal.get_todays_events.return_value = []
        mock_gcal.get_free_time_blocks.return_value = []
        mock_gcal.validate_credentials.return_value = (True, "Valid")
        mock_gcal_cls.return_value = mock_gcal
        mock_gcal_cls.validate_credentials_static.return_value = (True, "Valid")

        mock_llm = MagicMock()
        mock_llm.plan_triage.return_value = mock_plan
        mock_llm_cls.from_env.return_value = mock_llm
        mock_llm_cls.validate_backend.return_value = (True, "Valid")

        plan = run_cli(auto=True, dry_run=True, sync=True)

        assert plan == mock_plan
        mock_todoist.sync_plan_tags.assert_not_called()
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out


def test_cli_sync_flag():
    """Test CLI sync flag triggers Todoist sync when dry_run is False."""
    mock_tasks = [
        Task(id="t1", content="Big task", project_id="p1", due_date=date.today(), priority=4)
    ]
    mock_plan = TriagePlan(big=mock_tasks, quadrant="urgent_important", domain="work")

    with (
        patch("cli.TodoistService") as mock_todoist_cls,
        patch("cli.GCalService") as mock_gcal_cls,
        patch("cli.LLMService") as mock_llm_cls,
    ):
        mock_todoist = MagicMock()
        mock_todoist.get_todays_tasks.return_value = mock_tasks
        mock_todoist.validate_credentials.return_value = (True, "Valid")
        mock_todoist_cls.return_value = mock_todoist

        mock_gcal = MagicMock()
        mock_gcal.get_todays_events.return_value = []
        mock_gcal.get_free_time_blocks.return_value = []
        mock_gcal.validate_credentials.return_value = (True, "Valid")
        mock_gcal_cls.return_value = mock_gcal
        mock_gcal_cls.validate_credentials_static.return_value = (True, "Valid")

        mock_llm = MagicMock()
        mock_llm.plan_triage.return_value = mock_plan
        mock_llm_cls.from_env.return_value = mock_llm
        mock_llm_cls.validate_backend.return_value = (True, "Valid")

        plan = run_cli(auto=True, dry_run=False, sync=True)

        assert plan == mock_plan
        mock_todoist.sync_plan_tags.assert_called_once()


def test_render_triage_plan_includes_postponed_tasks(capsys):
    """Verify render_triage_plan displays POSTPONED tasks and stale badges."""
    from cli import render_triage_plan

    postponed_task = Task(
        id="p1", content="Postponed task", project_id="proj", due_date=date.today(), days_overdue=8
    )
    plan = TriagePlan(big=[], medium=[], small=[], postponed=[postponed_task])
    render_triage_plan(plan, schedule_events=[], free_blocks=[])
    captured = capsys.readouterr()
    assert "POSTPONED" in captured.out
    assert "Postponed task" in captured.out
    assert "STALE" in captured.out


def test_cli_precheck_gcal_failure(capsys):
    """CLI pre-checks fail cleanly when GCal OAuth token is missing."""
    import pytest

    with (
        patch("cli.TodoistService") as mock_todoist_cls,
        patch("cli.GCalService") as mock_gcal_cls,
        patch("cli.LLMService") as mock_llm_cls,
    ):
        mock_todoist = MagicMock()
        mock_todoist.validate_credentials.return_value = (True, "Valid")
        mock_todoist_cls.return_value = mock_todoist

        mock_gcal = MagicMock()
        mock_gcal_cls.return_value = mock_gcal
        mock_gcal_cls.validate_credentials_static.return_value = (
            False,
            "Google Calendar OAuth token missing",
        )

        mock_llm = MagicMock()
        mock_llm_cls.validate_backend.return_value = (True, "Valid")
        mock_llm_cls.get_available_backends.return_value = {"default": MagicMock()}
        mock_llm_cls.from_env.return_value = mock_llm

        with pytest.raises(SystemExit):
            run_cli(auto=True)

        mock_gcal.get_todays_events.assert_not_called()
        captured = capsys.readouterr()
        assert "Google Calendar OAuth token missing" in captured.out
