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
        mock_todoist_cls.return_value = mock_todoist

        mock_gcal = MagicMock()
        mock_gcal.get_todays_events.return_value = mock_events
        mock_gcal.get_free_time_blocks.return_value = mock_blocks
        mock_gcal_cls.return_value = mock_gcal

        mock_llm = MagicMock()
        mock_llm.plan_triage.return_value = mock_plan
        mock_llm_cls.return_value = mock_llm
        mock_llm_cls.from_env.return_value = mock_llm

        plan = run_cli(auto=True)

        assert plan == mock_plan
        captured = capsys.readouterr()
        assert "Autonomous morning triage completed" in captured.out or "Plan" in captured.out
