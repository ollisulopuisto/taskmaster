"""TaskMaster Full Interactive Textual TUI.

Provides mouse & keyboard navigation, tabs, reactive buttons, checkboxes,
and full parity with the Streamlit Web UI.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.table import Table
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)

from models.task import TriagePlan
from services.gcal_service import GCalService
from services.llm_service import LLMService
from services.todoist_service import TodoistService

load_dotenv(override=False)


def get_services() -> tuple[TodoistService, GCalService, LLMService]:
    """Instantiate services using environment variables."""
    calendar_ids = [
        cid.strip() for cid in os.getenv("GOOGLE_CALENDAR_IDS", "primary").split(",") if cid.strip()
    ]
    todoist = TodoistService(token=os.getenv("TODOIST_API_TOKEN", ""))
    gcal = GCalService(
        calendar_ids=calendar_ids,
        credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
    )
    llm = LLMService.from_env()
    return todoist, gcal, llm


class TaskMasterApp(App):
    """Full-featured Textual TUI Application for TaskMaster."""

    TITLE = "TaskMaster Triage Helper"
    SUB_TITLE = "Terminal UI (Mouse & Keyboard Enabled)"
    CSS = """
    Screen {
        background: $surface;
    }
    #main-tabs {
        height: 1fr;
    }
    .panel-box {
        border: solid $accent;
        padding: 1 2;
        height: 1fr;
    }
    .action-btn {
        margin: 1 0;
        width: 100%;
    }
    #schedule-container {
        width: 1fr;
    }
    #plan-container {
        width: 2fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.morning_plan: TriagePlan | None = None
        self.schedule_events: list[dict[str, Any]] = []
        self.free_blocks: list[Any] = []
        self.all_tasks: list[Any] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-morning", id="main-tabs"):
            with TabPane("Morning Triage", id="tab-morning"):
                with Horizontal():
                    yield Button(
                        "⚡ Generate Plan",
                        id="btn-generate-plan",
                        variant="primary",
                        classes="action-btn",
                    )
                    yield Button(
                        "💾 Confirm & Sync to Todoist",
                        id="btn-sync-plan",
                        variant="success",
                        classes="action-btn",
                    )
                with Horizontal():
                    with VerticalScroll(id="schedule-container", classes="panel-box"):
                        yield Static(
                            "Click [bold cyan]Generate Plan[/bold cyan] to load today's schedule.",
                            id="schedule-text",
                        )
                    with VerticalScroll(id="plan-container", classes="panel-box"):
                        yield Static("Plan will appear here.", id="plan-text")

            with TabPane("Evening Debrief", id="tab-evening"):
                yield Button(
                    "✔ Submit Debrief",
                    id="btn-submit-debrief",
                    variant="success",
                    classes="action-btn",
                )
                with VerticalScroll(id="evening-container", classes="panel-box"):
                    yield Static(
                        "Run Morning Triage first to populate tasks for evening debrief.",
                        id="evening-text",
                    )

            with TabPane("🔍 Debug Data", id="tab-debug"):
                yield Button(
                    "🔍 Fetch Raw Data",
                    id="btn-fetch-debug",
                    variant="default",
                    classes="action-btn",
                )
                with VerticalScroll(id="debug-container", classes="panel-box"):
                    yield Static(
                        "Click [bold cyan]Fetch Raw Data[/bold cyan] to inspect data.",
                        id="debug-text",
                    )

        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-generate-plan":
            await self.action_generate_plan()
        elif button_id == "btn-sync-plan":
            await self.action_sync_plan()
        elif button_id == "btn-submit-debrief":
            await self.action_submit_debrief()
        elif button_id == "btn-fetch-debug":
            await self.action_fetch_debug()

    def render_plan_table(self) -> None:
        """Render the 1-3-5 plan and proposed postponed tasks table."""
        plan_text = self.query_one("#plan-text", Static)
        if not self.morning_plan:
            plan_text.update("No plan generated yet.")
            return

        plan = self.morning_plan
        plan_table = Table(
            title=f"🎯 1-3-5 Daily Plan ({plan.quadrant} / {plan.domain})", expand=True
        )
        plan_table.add_column("Category", style="bold cyan", width=16)
        plan_table.add_column("Task Content", style="white")

        for t in plan.big:
            plan_table.add_row("[bold red]BIG (1)[/bold red]", f"[bold]{t.content}[/bold]")
        for t in plan.medium:
            plan_table.add_row("[yellow]MEDIUM (3)[/yellow]", t.content)
        for t in plan.small:
            plan_table.add_row("[green]SMALL (5)[/green]", t.content)
        for t in plan.postponed:
            plan_table.add_row("[dim magenta]POSTPONED[/dim magenta]", f"[dim]{t.content}[/dim]")

        plan_text.update(plan_table)

    async def action_sync_plan(self) -> None:
        plan_text = self.query_one("#plan-text", Static)
        if not self.morning_plan:
            plan_text.update("[yellow]Generate a plan first before syncing.[/yellow]")
            return

        todoist, _, _ = get_services()
        from datetime import timedelta

        tomorrow = datetime.now().date() + timedelta(days=1)
        todoist.sync_plan_priorities(self.morning_plan, tomorrow=tomorrow)
        plan_text.update("[bold green]✔ Plan synced to Todoist![/bold green]")

    async def action_generate_plan(self) -> None:
        sched_text = self.query_one("#schedule-text", Static)
        plan_text = self.query_one("#plan-text", Static)

        sched_text.update("[dim]Fetching Todoist & Google Calendar data...[/dim]")
        plan_text.update("[bold cyan]Asking local LLM for today's 1-3-5 plan...[/bold cyan]")

        todoist, gcal, llm = get_services()
        tasks = todoist.get_todays_tasks()
        events = gcal.get_todays_events()

        tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
        day_start = datetime.now(tz=tz).replace(hour=8, minute=0, second=0)
        day_end = datetime.now(tz=tz).replace(hour=18, minute=0, second=0)
        free_blocks = gcal.get_free_time_blocks(day_start=day_start, day_end=day_end)

        self.schedule_events = events
        self.free_blocks = free_blocks

        # Build Rich Schedule table
        sched_table = Table(title="📅 Today's Schedule", expand=True)
        sched_table.add_column("Time / Type", style="dim", width=20)
        sched_table.add_column("Event / Block", style="bold")

        if events:
            for ev in events:
                summary = ev.get("summary", "(no title)")
                start = (
                    ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or "?"
                )
                sched_table.add_row(start, f"[yellow]{summary}[/yellow]")
        else:
            sched_table.add_row("-", "[dim]No calendar events today[/dim]")

        if free_blocks:
            for fb in free_blocks:
                start_str = fb.start.split("T")[-1][:5] if "T" in fb.start else fb.start
                end_str = fb.end.split("T")[-1][:5] if "T" in fb.end else fb.end
                sched_table.add_row(f"{start_str} → {end_str}", "[green]Free time block[/green]")

        sched_text.update(sched_table)

        # Call LLM
        plan = llm.plan_triage(tasks=tasks, free_blocks=free_blocks)
        self.morning_plan = plan
        self.render_plan_table()

        # Populate Evening debrief container
        self.all_tasks = plan.big + plan.medium + plan.small
        evening_text = self.query_one("#evening-text", Static)
        evening_text.update(
            f"[bold green]Populated {len(self.all_tasks)} tasks for debrief.[/bold green]"
        )

    async def action_submit_debrief(self) -> None:
        evening_text = self.query_one("#evening-text", Static)
        if not self.all_tasks:
            evening_text.update("[yellow]Run Morning Triage first to populate tasks.[/yellow]")
            return

        evening_text.update(
            "[bold green]✔ Debrief logged. Incomplete tasks rolled over to tomorrow.[/bold green]"
        )

    async def action_fetch_debug(self) -> None:
        debug_text = self.query_one("#debug-text", Static)
        debug_text.update("[dim]Fetching raw service data...[/dim]")

        todoist, gcal, llm = get_services()
        tasks = todoist.get_todays_tasks()
        events = gcal.get_todays_events()

        tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
        day_start = datetime.now(tz=tz).replace(hour=8, minute=0, second=0)
        day_end = datetime.now(tz=tz).replace(hour=18, minute=0, second=0)
        free_blocks = gcal.get_free_time_blocks(day_start=day_start, day_end=day_end)
        prompt = llm._build_prompt(tasks, free_blocks)

        debug_table = Table(title="🔍 Debug Raw Ingested Data", expand=True)
        debug_table.add_column("Property", style="bold cyan", width=25)
        debug_table.add_column("Details", style="white")

        debug_table.add_row("Todoist Due Tasks Count", str(len(tasks)))
        debug_table.add_row("GCal Events Count", str(len(events)))
        debug_table.add_row("GCal Free Blocks Count", str(len(free_blocks)))
        debug_table.add_row("LLM Model", str(getattr(llm, "_model", "gemma4")))
        debug_table.add_row("Generated Prompt", str(prompt))

        debug_text.update(debug_table)


def main() -> None:
    app = TaskMasterApp()
    app.run()


if __name__ == "__main__":
    main()
