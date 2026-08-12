"""TaskMaster Full Interactive Textual TUI.

Provides mouse & keyboard navigation, tabs, reactive buttons, checkboxes,
and full parity with the Streamlit Web UI.
"""

from __future__ import annotations

import asyncio
import os
import time
import webbrowser
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.table import Table
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
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


def format_duration(seconds: float) -> str:
    """Format duration in seconds into a human-readable string (e.g. '14.2s' or '2m 15s')."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remaining_sec = round(seconds % 60)
    if remaining_sec == 60:
        minutes += 1
        remaining_sec = 0
    if remaining_sec == 0:
        return f"{minutes}m"
    return f"{minutes}m {remaining_sec}s"


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


class AuthModal(ModalScreen[str]):
    """Modal showing the Google auth URL and collecting the pasted code."""

    BINDINGS = [("escape", "dismiss_modal", "Cancel")]
    CSS = """
    AuthModal {
        align: center middle;
        background: $background;
    }
    #auth-dialog {
        width: 88;
        max-height: 22;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #auth-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #auth-url {
        width: 1fr;
        height: auto;
        background: $panel;
        border: round $primary;
        padding: 0 1;
        margin-bottom: 1;
    }
    #auth-instructions {
        height: auto;
        margin-bottom: 1;
    }
    #auth-code {
        margin-bottom: 1;
    }
    #auth-buttons {
        height: 3;
    }
    #auth-buttons Button {
        width: 1fr;
        margin: 0 1;
    }
    """

    def __init__(self, auth_url: str) -> None:
        super().__init__()
        self.auth_url = auth_url

    def compose(self) -> ComposeResult:
        with Vertical(id="auth-dialog"):
            yield Static("🔑 Google Calendar Authorization", id="auth-title")
            yield Static(self.auth_url, id="auth-url")
            yield Static(
                "1. Press 'Open URL' (or copy the URL above) and allow access in your browser.\n"
                "2. The redirect lands on a localhost page that fails to load — that is normal.\n"
                "3. Copy the value of the `code` parameter from the address bar and paste it\n"
                "   below, then press Submit (or press Escape to cancel).",
                id="auth-instructions",
            )
            yield Input(placeholder="Paste the authorization code...", id="auth-code")
            with Horizontal(id="auth-buttons"):
                yield Button("Open URL", id="btn-auth-open", variant="default")
                yield Button("Submit", id="btn-auth-submit", variant="primary")
                yield Button("Cancel", id="btn-auth-cancel", variant="error")

    @on(Button.Pressed, "#btn-auth-submit")
    def submit_auth_code(self) -> None:
        code = self.query_one("#auth-code", Input).value.strip()
        if code:
            self.dismiss(code)

    @on(Button.Pressed, "#btn-auth-cancel")
    def cancel_auth(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#btn-auth-open")
    def open_auth_url(self) -> None:
        webbrowser.open(self.auth_url)

    @on(Input.Submitted, "#auth-code")
    def submit_auth_code_on_enter(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value.strip())

    def action_dismiss_modal(self) -> None:
        self.dismiss()


class TaskMasterApp(App):
    """Full-featured Textual TUI Application for TaskMaster."""

    TITLE = "TaskMaster Triage Helper"
    SUB_TITLE = "Terminal UI (Mouse & Keyboard Enabled)"
    BINDINGS = [
        ("a", "auth_gcal", "🔑 OAuth GCal"),
        ("g", "generate_plan", "⚡ Generate Plan"),
        ("s", "sync_plan", "💾 Confirm & Sync"),
        ("v", "save_settings", "💾 Save Settings"),
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
        self._oauth_flow: Any = None

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
                        "🔑 OAuth GCal (A)",
                        id="btn-auth-gcal",
                        variant="default",
                        classes="action-btn",
                    )
                    yield Button(
                        "🔄 Discover LLMs",
                        id="btn-discover-llm",
                        variant="default",
                        classes="action-btn",
                    )
                    yield Button(
                        "💾 Save Settings (V)",
                        id="btn-save-settings",
                        variant="default",
                        classes="action-btn",
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
        elif button_id == "btn-auth-gcal":
            await self.action_auth_gcal()
        elif button_id == "btn-discover-llm":
            await self.action_discover_llms()
        elif button_id == "btn-save-settings":
            await self.action_save_settings()
        elif button_id == "btn-generate-plan":
            await self.action_generate_plan()
        elif button_id == "btn-sync-plan":
            await self.action_sync_plan()
        elif button_id == "btn-submit-debrief":
            await self.action_submit_debrief()
        elif button_id == "btn-fetch-debug":
            await self.action_fetch_debug()

    async def action_auth_gcal(self, _auth_timeout: float = 120.0) -> None:
        """Start automatic in-app OAuth flow for Google Calendar.

        Spins up a local loopback HTTP server on a random port, opens the
        consent URL in the browser, and waits up to ``_auth_timeout`` seconds
        for Google to redirect back.  The authorization code is captured
        automatically — no copy-paste required.
        """
        plan_text = self.query_one("#plan-text", Static)
        _, gcal, _ = get_services()

        try:
            flow, url, done_event, code_box = await asyncio.to_thread(gcal.start_local_auth_server)
            self._oauth_flow = flow
        except Exception as exc:
            plan_text.update(f"[bold red]❌ Failed to start GCal OAuth flow: {exc}[/bold red]")
            return

        # Show the URL and open it; the user just approves in the browser.
        from rich.markup import escape as rich_escape

        plan_text.update(
            "[bold cyan]🔑 Google Calendar OAuth[/bold cyan]\n\n"
            "Opening browser… if it doesn't open automatically, visit:\n"
            f"{rich_escape(url)}\n\n"
            "[dim]Waiting for browser approval (timeout: 2 min)…[/dim]"
        )
        webbrowser.open(url)

        # Wait in a background thread so the TUI stays responsive.
        completed = await asyncio.to_thread(done_event.wait, _auth_timeout)

        if not completed:
            plan_text.update(
                "[bold red]❌ OAuth timed out (2 min). Press 🔑 OAuth GCal to try again.[/bold red]"
            )
            return

        code = code_box[0]
        if not code:
            plan_text.update("[bold red]❌ No authorization code received.[/bold red]")
            return

        ok, msg = await asyncio.to_thread(gcal.complete_auth, flow, code)
        if ok:
            plan_text.update(f"[bold green]✔ {msg}[/bold green]")
        else:
            plan_text.update(f"[bold red]❌ OAuth exchange failed: {msg}[/bold red]")

    async def action_save_settings(self) -> None:
        """Save selected LLM backend settings to .env file."""
        plan_text = self.query_one("#plan-text", Static)
        backend_select = self.query_one("#select-llm-backend", Select)
        selected_backend = str(backend_select.value) if backend_select.value else None

        if not selected_backend:
            plan_text.update("[yellow]No LLM backend selected to save.[/yellow]")
            return

        backends = await asyncio.to_thread(LLMService.get_available_backends, autodiscover=True)
        config = backends.get(selected_backend)

        try:
            await asyncio.to_thread(
                LLMService.save_settings_to_env, selected_backend, config=config
            )
            plan_text.update(
                "[bold green]✔ Saved settings to .env "
                f"(Default LLM: {selected_backend})[/bold green]"
            )

        except Exception as exc:
            plan_text.update(f"[bold red]❌ Failed to save settings to .env: {exc}[/bold red]")

    async def action_discover_llms(self) -> None:
        """Autodiscover local and LAN LLM servers and update backend dropdown options."""
        plan_text = self.query_one("#plan-text", Static)
        plan_text.update(
            "[dim]Discovering LLMs (mDNS LAN browse + local ports 8000, 11434, 1234)...[/dim]"
        )

        backends = await asyncio.to_thread(LLMService.get_available_backends, autodiscover=True)
        options = [(cfg.name, cfg.key) for cfg in backends.values()]

        backend_select = self.query_one("#select-llm-backend", Select)
        curr_val = backend_select.value
        backend_select.set_options(options)

        if curr_val in [opt[1] for opt in options]:
            backend_select.value = curr_val
        elif options:
            backend_select.value = options[0][1]

        disc_count = sum(1 for k in backends if k.startswith("auto_"))
        plan_text.update(
            f"[bold green]✔ LLM Discovery complete: Found {len(backends)} backend(s) "
            f"({disc_count} autodiscovered local/LAN model(s)).[/bold green]"
        )

    async def render_plan_table(self) -> None:
        """Render the 1-3-5 plan and proposed postponed tasks with interactive reassign buttons."""
        plan_container = self.query_one("#plan-container", VerticalScroll)
        await plan_container.remove_children()

        if not self.morning_plan:
            await plan_container.mount(Static("No plan generated yet.", id="plan-text"))
            return

        plan = self.morning_plan
        dur_str = (
            f" · ⏱️ {format_duration(self.last_elapsed_sec)}"
            if self.last_elapsed_sec is not None
            else ""
        )
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
        plan_text.update("[dim]Syncing plan tags to Todoist...[/dim]")
        try:
            await asyncio.to_thread(todoist.sync_plan_tags, self.morning_plan, tomorrow=tomorrow)
            plan_text.update("[bold green]✔ Plan synced to Todoist![/bold green]")
        except Exception as exc:
            plan_text.update(f"[bold red]❌ Todoist Sync Error: {exc}[/bold red]")

    async def action_generate_plan(self) -> None:
        start_time = time.perf_counter()
        sched_text = self.query_one("#schedule-text", Static)
        plan_text = self.query_one("#plan-text", Static)

        backend_select = self.query_one("#select-llm-backend", Select)
        selected_backend = str(backend_select.value) if backend_select.value else None
        self.last_selected_backend = selected_backend

        plan_text.update("[dim]Running API credential & server pre-checks...[/dim]")

        # 1. Pre-check Google Calendar OAuth credentials statically
        credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        gc_ok, gc_msg = await asyncio.to_thread(
            GCalService.validate_credentials_static, credentials_path=credentials_path
        )
        if not gc_ok:
            plan_text.update(f"[bold red]❌ Google Calendar Pre-check Failed: {gc_msg}[/bold red]")
            return

        # 2. Pre-check selected LLM backend
        backends = LLMService.get_available_backends()
        target_cfg = None
        if isinstance(backends, dict) and backends:
            target_cfg = backends.get(selected_backend or "") or next(iter(backends.values()), None)

        if target_cfg:
            llm_ok, llm_msg = await asyncio.to_thread(LLMService.validate_backend, target_cfg)
            if not llm_ok:
                plan_text.update(f"[bold red]❌ LLM Pre-check Failed: {llm_msg}[/bold red]")
                return

        todoist, gcal, llm = get_services(backend_key=selected_backend)

        # 3. Pre-check Todoist API token
        td_ok, td_msg = await asyncio.to_thread(todoist.validate_credentials)
        if not td_ok:
            plan_text.update(f"[bold red]❌ Todoist Pre-check Failed: {td_msg}[/bold red]")
            return

        plan_text.update("[dim]Ingesting Todoist tasks & Google Calendar schedule...[/dim]")

        try:
            tasks = await asyncio.to_thread(todoist.get_todays_tasks)
            events = await asyncio.to_thread(gcal.get_todays_events)

            tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
            day_start = datetime.now(tz=tz).replace(hour=8, minute=0, second=0)
            day_end = datetime.now(tz=tz).replace(hour=18, minute=0, second=0)
            free_blocks = await asyncio.to_thread(
                gcal.get_free_time_blocks, day_start=day_start, day_end=day_end
            )
        except Exception as exc:
            plan_text.update(f"[bold red]❌ Ingestion Error (Todoist/GCal): {exc}[/bold red]")
            return

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

        # Call LLM with live ticking timer and timeout protection
        plan_text.update(
            f"[bold cyan]Asking LLM ({selected_backend}) for today's 1-3-5 plan... "
            "⏳ (0s elapsed)[/bold cyan]"
        )
        try:

            async def _run_llm() -> TriagePlan:
                return await asyncio.to_thread(
                    llm.plan_triage, tasks=tasks, free_blocks=free_blocks
                )

            llm_task = asyncio.create_task(_run_llm())
            t0 = time.perf_counter()
            while not llm_task.done():
                elapsed = round(time.perf_counter() - t0)
                plan_text.update(
                    f"[bold cyan]Asking LLM ({selected_backend}) for today's 1-3-5 plan... "
                    f"⏳ ({elapsed}s elapsed)[/bold cyan]"
                )
                done, _ = await asyncio.wait([llm_task], timeout=0.5)
                if not done and elapsed >= 120:
                    llm_task.cancel()
                    raise TimeoutError("LLM call timed out after 120s")

            plan = llm_task.result()
            self.last_elapsed_sec = time.perf_counter() - start_time
            self.morning_plan = plan
            await self.render_plan_table()

            # Populate Evening debrief container
            self.all_tasks = plan.big + plan.medium + plan.small
            evening_text = self.query_one("#evening-text", Static)
            evening_text.update(
                f"[bold green]Populated {len(self.all_tasks)} tasks for debrief "
                f"(generated in {format_duration(self.last_elapsed_sec)}).[/bold green]"
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
        evening_text.update("[dim]Rolling over tasks in Todoist...[/dim]")
        try:
            for task in self.all_tasks:
                await asyncio.to_thread(todoist.postpone_task, task.id, tomorrow)

            evening_text.update(
                f"[bold green]✔ Debrief logged. {len(self.all_tasks)} "
                "tasks rolled over.[/bold green]"
            )
        except Exception as exc:
            evening_text.update(f"[bold red]❌ Debrief Error: {exc}[/bold red]")

    async def action_fetch_debug(self) -> None:
        debug_text = self.query_one("#debug-text", Static)
        debug_text.update("[dim]Running credential pre-checks...[/dim]")

        backend_select = self.query_one("#select-llm-backend", Select)
        selected_backend = str(backend_select.value) if backend_select.value else None

        # Pre-check credentials statically before instantiating services to prevent OAuth flow hangs
        credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        gc_ok, gc_msg = await asyncio.to_thread(
            GCalService.validate_credentials_static, credentials_path=credentials_path
        )
        if not gc_ok:
            debug_text.update(f"[bold red]❌ Google Calendar Pre-check Failed: {gc_msg}[/bold red]")
            return

        todoist, gcal, llm = get_services(backend_key=selected_backend)

        td_ok, td_msg = await asyncio.to_thread(todoist.validate_credentials)
        if not td_ok:
            debug_text.update(f"[bold red]❌ Todoist Pre-check Failed: {td_msg}[/bold red]")
            return

        debug_text.update("[dim]Fetching raw service data...[/dim]")
        try:
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
        except Exception as exc:
            debug_text.update(f"[bold red]❌ Raw Data Fetch Error: {exc}[/bold red]")


def main() -> None:
    app = TaskMasterApp()
    app.run()


if __name__ == "__main__":
    main()
