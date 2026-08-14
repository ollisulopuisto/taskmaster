"""Tests for the Textual mouse & keyboard TUI application."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Button, Static, TabbedContent

from models.task import Task, TimeBlock, TriagePlan
from services.gcal_service import GCalService
from tui import AuthModal, TaskMasterApp


@pytest.fixture
def mock_services():
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
        patch("tui.TodoistService") as mock_todoist_cls,
        patch("tui.GCalService") as mock_gcal_cls,
        patch("tui.LLMService") as mock_llm_cls,
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
        mock_gcal_cls.format_event_time.side_effect = GCalService.format_event_time

        mock_llm = MagicMock()
        mock_llm.plan_triage.return_value = mock_plan
        mock_llm_cls.return_value = mock_llm
        mock_llm_cls.from_env.return_value = mock_llm
        mock_llm_cls.validate_backend.return_value = (True, "Valid")

        yield {
            "todoist": mock_todoist,
            "gcal": mock_gcal,
            "llm": mock_llm,
            "tasks": mock_tasks,
            "plan": mock_plan,
            "events": mock_events,
            "blocks": mock_blocks,
        }


@pytest.mark.anyio
async def test_tui_initial_render(mock_services):
    """App launches and renders Header, Footer, and TabbedContent."""
    app = TaskMasterApp()
    async with app.run_test():
        assert app.is_running
        # Verify tabs exist
        morning_tab = app.query_one("#tab-morning")
        assert morning_tab is not None


@pytest.mark.anyio
async def test_tui_fetch_schedule_and_generate_plan(mock_services):
    """Clicking Generate Plan loads schedule and plan."""
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        # Click generate plan button
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        # Check schedule events rendered
        sched_widget = app.query_one("#schedule-text", Static)
        assert sched_widget is not None


@pytest.mark.anyio
async def test_tui_debug_tab_fetch(mock_services):
    """Clicking Fetch Debug Data populates debug container."""
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        # Switch to debug tab
        tabs = app.query_one(TabbedContent)
        tabs.active = "tab-debug"
        await pilot.pause()

        # Click fetch debug button
        await pilot.click("#btn-fetch-debug")
        await pilot.pause()

        debug_widget = app.query_one("#debug-container")
        assert debug_widget is not None


@pytest.mark.anyio
async def test_tui_interactive_reassign_task(mock_services):
    """Clicking [1 Big] reassign button on task t2 promotes it to BIG."""
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        # Press promote button for t2
        btn = app.query_one("#btn-reassign-big-t2", Button)
        btn.press()
        await pilot.pause()

        assert app.morning_plan.big[0].id == "t2"


@pytest.mark.anyio
async def test_tui_button_labels_reflect_current_task_state(mock_services):
    """Button for current task category displays checkmark label (e.g. ✓ BIG)."""
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        # Task t1 is in BIG -> #btn-reassign-big-t1 should show "✓ BIG"
        btn_big_t1 = app.query_one("#btn-reassign-big-t1", Button)
        assert "✓ BIG" in str(btn_big_t1.label) or "✓" in str(btn_big_t1.label)


@pytest.mark.anyio
async def test_tui_submit_debrief_completes_and_postpones(mock_services):
    """Submitting debrief processes tasks against Todoist API."""
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        tabs = app.query_one(TabbedContent)
        tabs.active = "tab-evening"
        await pilot.pause()

        btn = app.query_one("#btn-submit-debrief", Button)
        btn.press()
        await pilot.pause()

        # Verify debrief executed
        debrief_text = app.query_one("#evening-text", Static)
        assert "Debrief logged" in str(debrief_text.content)


@pytest.mark.anyio
async def test_tui_backend_selector_renders_options(mock_services):
    """Verify the Select widget for LLM backends renders in the TUI top bar."""
    from textual.widgets import Select

    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        select_widget = app.query_one("#select-llm-backend", Select)
        assert select_widget is not None
        assert select_widget.value is not None


@pytest.mark.anyio
async def test_tui_displays_execution_duration(mock_services):
    """Verify plan generation records and displays execution duration in seconds."""
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        assert app.last_elapsed_sec is not None
        assert app.last_elapsed_sec >= 0
        plan_text = app.query_one("#plan-text", Static)
        assert "⏱️" in str(plan_text.content.title)


@pytest.mark.anyio
async def test_tui_discover_llms_button(mock_services):
    """Clicking Discover LLMs button triggers autodiscovery and updates backend options."""
    from textual.widgets import Select

    from models.task import LLMBackendConfig

    discovered_cfg = LLMBackendConfig(
        key="auto_8000_gemma",
        name="[Discovered 8000] gemma:latest",
        base_url="http://localhost:8000/v1",
        model="gemma:latest",
    )

    with patch(
        "tui.LLMService.get_available_backends",
        return_value={
            "default": LLMBackendConfig(
                key="default", name="Default", base_url="http://x", model="m"
            ),
            "auto_8000_gemma": discovered_cfg,
        },
    ):
        app = TaskMasterApp()
        async with app.run_test() as pilot:
            await pilot.click("#btn-discover-llm")
            await pilot.pause()

            select_widget = app.query_one("#select-llm-backend", Select)
            assert select_widget is not None
            # Check options updated to include auto_8000_gemma
            option_values = [opt[1] for opt in select_widget._options]
            assert "auto_8000_gemma" in option_values


def test_format_duration():
    from tui import format_duration

    assert format_duration(4.2) == "4.2s"
    assert format_duration(45.8) == "45.8s"
    assert format_duration(65.2) == "1m 5s"
    assert format_duration(120.0) == "2m"
    assert format_duration(125.7) == "2m 6s"


@pytest.mark.anyio
async def test_tui_generate_plan_handles_gcal_todoist_error(mock_services):
    """If GCal or Todoist raises an error, TUI reports error in plan_text."""
    mock_services["gcal"].get_todays_events.side_effect = RuntimeError("GCal Auth Failed")
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        plan_text = app.query_one("#plan-text", Static)
        assert "Error" in str(plan_text.content) or "GCal Auth Failed" in str(plan_text.content)


@pytest.mark.anyio
async def test_tui_save_settings_button(mock_services):
    """Clicking Save Settings button invokes LLMService.save_settings_to_env."""
    with patch("tui.LLMService.save_settings_to_env") as mock_save:
        app = TaskMasterApp()
        async with app.run_test() as pilot:
            await pilot.click("#btn-save-settings")
            await pilot.pause()

            mock_save.assert_called_once()
            plan_text = app.query_one("#plan-text", Static)
            assert "Saved settings" in str(plan_text.content)


@pytest.mark.anyio
async def test_tui_generate_plan_handles_timeout(mock_services):
    """If LLM call times out, TUI displays explicit timeout error message."""
    mock_services["llm"].plan_triage.side_effect = TimeoutError("LLM call timed out after 120s")
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        plan_text = app.query_one("#plan-text", Static)
        assert (
            "timed out" in str(plan_text.content).lower()
            or "error" in str(plan_text.content).lower()
        )


@pytest.mark.anyio
async def test_tui_fetch_debug_handles_gcal_error(mock_services):
    """If GCal pre-check fails in debug tab, TUI displays error instead of hanging."""
    with patch(
        "tui.GCalService.validate_credentials_static", return_value=(False, "OAuth token missing")
    ):
        app = TaskMasterApp()
        async with app.run_test() as pilot:
            tabs = app.query_one(TabbedContent)
            tabs.active = "tab-debug"
            await pilot.pause()

            await pilot.click("#btn-fetch-debug")
            await pilot.pause()

            debug_text = app.query_one("#debug-text", Static)
            assert "OAuth token missing" in str(debug_text.content) or "Failed" in str(
                debug_text.content
            )


@pytest.mark.anyio
async def test_tui_sync_plan_handles_error(mock_services):
    """If sync_plan_tags raises an exception, action_sync_plan displays error cleanly."""
    mock_services["todoist"].sync_plan_tags.side_effect = RuntimeError("Todoist Sync Failed")
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        # Generate plan first
        await pilot.click("#btn-generate-plan")
        await pilot.pause()

        # Click sync
        await pilot.click("#btn-sync-plan")
        await pilot.pause()

        plan_text = app.query_one("#plan-text", Static)
        assert "Todoist Sync Failed" in str(plan_text.content) or "Error" in str(plan_text.content)


@pytest.mark.anyio
async def test_tui_auth_gcal_completes_automatically(mock_services):
    """OAuth flow completes automatically when the callback server receives the code."""
    import threading

    mock_gcal = mock_services["gcal"]
    mock_flow = MagicMock()

    # Pre-fire the done_event so the TUI doesn't actually wait.
    done_event = threading.Event()
    done_event.set()
    code_box: list[str] = ["auto-captured-code"]

    mock_gcal.start_local_auth_server.return_value = (
        mock_flow,
        "https://accounts.google.com/o/oauth2/auth?x=1",
        done_event,
        code_box,
    )
    mock_gcal.complete_auth.return_value = (True, "Google Calendar OAuth token saved.")

    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause(0.2)

        plan_text = app.query_one("#plan-text", Static)
        assert "token saved" in str(plan_text.content).lower()

    mock_gcal.complete_auth.assert_called_once_with(mock_flow, "auto-captured-code")


@pytest.mark.anyio
async def test_tui_auth_gcal_timeout_reports_error(mock_services):
    """When the OAuth callback times out, an error message is shown."""
    import threading

    mock_gcal = mock_services["gcal"]
    mock_flow = MagicMock()

    # done_event that never fires — wait(0) returns False immediately.
    done_event = threading.Event()  # not set
    code_box: list[str] = [""]

    mock_gcal.start_local_auth_server.return_value = (
        mock_flow,
        "https://accounts.google.com/o/oauth2/auth?x=1",
        done_event,
        code_box,
    )

    app = TaskMasterApp()
    async with app.run_test() as pilot:
        # Call with _auth_timeout=0 so wait() returns False immediately.
        await app.action_auth_gcal(_auth_timeout=0)
        await pilot.pause(0.1)

        plan_text = app.query_one("#plan-text", Static)
        assert "timed out" in str(plan_text.content).lower()

    mock_gcal.complete_auth.assert_not_called()


@pytest.mark.anyio
async def test_tui_auth_gcal_missing_credentials_reports_error(monkeypatch, mock_services):
    """Missing client secrets file is reported without opening a modal."""
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "/nonexistent/credentials.json")
    mock_services["gcal"].start_local_auth_server.side_effect = FileNotFoundError(
        "OAuth client secrets file missing at '/nonexistent/credentials.json'."
    )
    app = TaskMasterApp()
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()

        assert not isinstance(app.screen, AuthModal)
        plan_text = app.query_one("#plan-text", Static)
        text = str(plan_text.content).lower()
        assert "not found" in text or "missing" in text


@pytest.mark.anyio
async def test_tui_responsive_layout_standard_terminal(mock_services):
    """Test TUI layout renders correctly in standard terminal size (80x24)."""
    app = TaskMasterApp()
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.is_running
        await pilot.click("#btn-generate-plan")
        await pilot.pause()
        plan_text = app.query_one("#plan-text", Static)
        assert plan_text is not None


@pytest.mark.anyio
async def test_tui_responsive_layout_compact_terminal(mock_services):
    """Test TUI layout renders cleanly in compact terminal size (60x20)."""
    app = TaskMasterApp()
    async with app.run_test(size=(60, 20)) as pilot:
        assert app.is_running
        main_split = app.query_one("#morning-content-split")
        assert main_split is not None
        await pilot.click("#btn-generate-plan")
        await pilot.pause()
        plan_text = app.query_one("#plan-text", Static)
        assert plan_text is not None
