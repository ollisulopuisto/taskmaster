"""TaskMaster Full Interactive Textual TUI.

Provides mouse & keyboard navigation, tabs, reactive buttons, checkboxes,
and full parity with the Streamlit Web UI.
"""

from __future__ import annotations

import asyncio
import os
import time
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
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from models.task import TriagePlan
from services.gcal_service import GCalService
from services.llm_service import LLMService
from services.todoist_service import TodoistService

load_dotenv(override=False)


def get_services(backend_key: str | None = None) -> tuple[TodoistService, GCalService, LLMService]:
    """Instantiate services using environment variables."""
    calendar_ids = [
        cid.strip() for cid in os.getenv("GOOGLE_CALENDAR_IDS", "primary").split(",") if cid.strip()
    ]
    todoist = TodoistService(token=os.getenv("TODOIST_API_TOKEN", ""))
    gcal = GCalService(
        calendar_ids=calendar_ids,
        credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
    )
    llm = LLMService.from_env(backend_key=backend_key)
    return todoist, gcal, llm


class TaskMasterApp(App):
    """Full-featured Textual TUI Application for TaskMaster."""

    TITLE = "TaskMaster Triage Helper"
    SUB_TITLE = "Terminal UI (Mouse & Keyboard Enabled)"
    BINDINGS = [
        ("g", "generate_plan", "⚡ Generate Plan"),
        ("s", "sync_plan", "💾 Confirm & Sync"),
        ("q", "quit", "Quit"),
    ]

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
    .top-bar {
        height: 3;
        margin: 1 0;
    }
    .action-btn {
        width: 1fr;
        margin: 0 1;
    }
    .backend-select {
        width: 1fr;
        margin: 0 1;
    }
    .task-row {
        height: 3;
        margin: 1 0;
        border-bottom: solid $primary;
    }
    .task-label {
        width: 1fr;
        content-align: left middle;
    }
    .mini-btn {
        min-width: 9;
        margin: 0 1;
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
        self.last_elapsed_sec: float | None = None
        self.last_selected_backend: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        backend_options: list[tuple[str, str]] = []
        try:
            backends = LLMService.get_available_backends()
            if isinstance(backends, dict):
                backend_options = [(cfg.name, cfg.key) for cfg in backends.values()]
        except Exception:
            pass

        if not backend_options:
            backend_options = [("Default LLM", "default")]

        default_backend = os.getenv("LLM_DEFAULT_BACKEND", "").strip()
        default_backend_key = (
            default_backend
            if default_backend in [opt[1] for opt in backend_options]
            else backend_options[0][1]
        )

        with TabbedContent(initial="tab-morning", id="main-tabs"):
            with TabPane("Morning Triage", id="tab-morning"):
                with Horizontal(classes="top-bar"):
                    yield Select(
                        backend_options,
                        value=default_backend_key,
                        id="select-llm-backend",
                        allow_blank=False,
                        classes="backend-select",
                    )
                    yield Button(
                        "⚡ Generate Plan (G)",
                        id="btn-generate-plan",
                        variant="primary",
                        classes="action-btn",
                    )
                    yield Button(
                        "💾 Confirm & Sync to Todoist (S)",
                        id="btn-sync-plan",
                        variant="success",
                        classes="action-btn",
                    )
                with Horizontal():
                    with VerticalScroll(id="schedule-container", classes="panel-box"):
                        yield Static(
                            "Click [bold cyan]Generate Plan[/bold cyan] or press [bold]G[/bold].",
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
        if button_id.startswith("btn-reassign-"):
            parts = button_id.split("-")
            target = parts[2]
            task_id = "-".join(parts[3:])
            if self.morning_plan:
                self.morning_plan = self.morning_plan.reassign_task(task_id, target)
                await self.render_plan_table()
        elif button_id == "btn-generate-plan":
            await self.action_generate_plan()
        elif button_id == "btn-sync-plan":
            await self.action_sync_plan()
        elif button_id == "btn-submit-debrief":
            await self.action_submit_debrief()
        elif button_id == "btn-fetch-debug":
            await self.action_fetch_debug()

    async def render_plan_table(self) -> None:
        """Render the 1-3-5 plan and proposed postponed tasks with interactive reassign buttons."""
        plan_container = self.query_one("#plan-container", VerticalScroll)
        await plan_container.remove_children()

        if not self.morning_plan:
            await plan_container.mount(Static("No plan generated yet.", id="plan-text"))
            return

        plan = self.morning_plan
        dur_str = f" · ⏱️ {self.last_elapsed_sec:.2f}s" if self.last_elapsed_sec is not None else ""
        plan_table = Table(
            title=f"🎯 1-3-5 Daily Plan ({plan.quadrant} / {plan.domain}){dur_str}", expand=True
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
            stale_badge = (
                f" [bold yellow][STALE {t.days_overdue}d][/bold yellow]" if t.is_stale else ""
            )
            plan_table.add_row(
                "[dim magenta]POSTPONED[/dim magenta]", f"[dim]{t.content}[/dim]{stale_badge}"
            )

        await plan_container.mount(Static(plan_table, id="plan-text"))

        # Mount interactive reassign buttons for each task
        all_items = [
            ("BIG", "big", plan.big),
            ("MEDIUM", "medium", plan.medium),
            ("SMALL", "small", plan.small),
            ("POSTPONED", "postponed", plan.postponed),
        ]
        for cat_name, cat_key, task_list in all_items:
            for t in task_list:
                btn_big = Button(
                    "✓ BIG" if cat_key == "big" else "1 Big",
                    id=f"btn-reassign-big-{t.id}",
                    variant="error" if cat_key == "big" else "default",
                    classes="mini-btn active-btn" if cat_key == "big" else "mini-btn",
                )
                btn_med = Button(
                    "✓ MED" if cat_key == "medium" else "3 Med",
                    id=f"btn-reassign-medium-{t.id}",
                    variant="warning" if cat_key == "medium" else "default",
                    classes="mini-btn active-btn" if cat_key == "medium" else "mini-btn",
                )
                btn_small = Button(
                    "✓ SMALL" if cat_key == "small" else "5 Small",
                    id=f"btn-reassign-small-{t.id}",
                    variant="success" if cat_key == "small" else "default",
                    classes="mini-btn active-btn" if cat_key == "small" else "mini-btn",
                )
                btn_post = Button(
                    "✓ POST" if cat_key == "postponed" else "P Postpone",
                    id=f"btn-reassign-postponed-{t.id}",
                    variant="primary" if cat_key == "postponed" else "default",
                    classes="mini-btn active-btn" if cat_key == "postponed" else "mini-btn",
                )
                row = Horizontal(
                    Static(f"[{cat_name}] [bold]{t.content}[/bold]", classes="task-label"),
                    btn_big,
                    btn_med,
                    btn_small,
                    btn_post,
                    classes="task-row",
                )
                await plan_container.mount(row)

    async def action_sync_plan(self) -> None:
        plan_text = self.query_one("#plan-text", Static)
        if not self.morning_plan:
            plan_text.update("[yellow]Generate a plan first before syncing.[/yellow]")
            return

        todoist, _, _ = get_services()
        from datetime import timedelta

        tomorrow = datetime.now().date() + timedelta(days=1)
        await asyncio.to_thread(todoist.sync_plan_priorities, self.morning_plan, tomorrow=tomorrow)
        plan_text.update("[bold green]✔ Plan synced to Todoist![/bold green]")

    async def action_generate_plan(self) -> None:
        start_time = time.perf_counter()
        sched_text = self.query_one("#schedule-text", Static)
        plan_text = self.query_one("#plan-text", Static)

        backend_select = self.query_one("#select-llm-backend", Select)
        selected_backend = str(backend_select.value) if backend_select.value else None
        self.last_selected_backend = selected_backend

        sched_text.update("[dim]Fetching Todoist & Google Calendar data...[/dim]")
        plan_text.update(
            f"[bold cyan]Asking LLM ({selected_backend}) for today's 1-3-5 plan...[/bold cyan]"
        )

        todoist, gcal, llm = get_services(backend_key=selected_backend)
        tasks = await asyncio.to_thread(todoist.get_todays_tasks)
        events = await asyncio.to_thread(gcal.get_todays_events)

        tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
        day_start = datetime.now(tz=tz).replace(hour=8, minute=0, second=0)
        day_end = datetime.now(tz=tz).replace(hour=18, minute=0, second=0)
        free_blocks = await asyncio.to_thread(
            gcal.get_free_time_blocks, day_start=day_start, day_end=day_end
        )

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
        try:
            plan = await asyncio.to_thread(llm.plan_triage, tasks=tasks, free_blocks=free_blocks)
            self.last_elapsed_sec = time.perf_counter() - start_time
            self.morning_plan = plan
            await self.render_plan_table()

            # Populate Evening debrief container
            self.all_tasks = plan.big + plan.medium + plan.small
            evening_text = self.query_one("#evening-text", Static)
            evening_text.update(
                f"[bold green]Populated {len(self.all_tasks)} tasks for debrief "
                f"(generated in {self.last_elapsed_sec:.2f}s).[/bold green]"
            )
        except Exception as exc:
            plan_text.update(f"[bold red]❌ LLM Error: {exc}[/bold red]")

    async def action_submit_debrief(self) -> None:
        evening_text = self.query_one("#evening-text", Static)
        if not self.all_tasks:
            evening_text.update("[yellow]Run Morning Triage first to populate tasks.[/yellow]")
            return

        todoist, _, _ = get_services()
        from datetime import timedelta

        tomorrow = datetime.now().date() + timedelta(days=1)
        for task in self.all_tasks:
            await asyncio.to_thread(todoist.postpone_task, task.id, tomorrow)

        evening_text.update(
            f"[bold green]✔ Debrief logged. {len(self.all_tasks)} tasks rolled over.[/bold green]"
        )

    async def action_fetch_debug(self) -> None:
        debug_text = self.query_one("#debug-text", Static)
        debug_text.update("[dim]Fetching raw service data...[/dim]")

        backend_select = self.query_one("#select-llm-backend", Select)
        selected_backend = str(backend_select.value) if backend_select.value else None

        todoist, gcal, llm = get_services(backend_key=selected_backend)
        tasks = await asyncio.to_thread(todoist.get_todays_tasks)
        events = await asyncio.to_thread(gcal.get_todays_events)

        tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
        day_start = datetime.now(tz=tz).replace(hour=8, minute=0, second=0)
        day_end = datetime.now(tz=tz).replace(hour=18, minute=0, second=0)
        free_blocks = await asyncio.to_thread(
            gcal.get_free_time_blocks, day_start=day_start, day_end=day_end
        )
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
