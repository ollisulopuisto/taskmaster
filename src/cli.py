"""TaskMaster TUI & Autonomous CLI.

Provides both an interactive terminal interface (TUI) and a fully autonomous
mode (--auto) designed to run via cron / LaunchAgent without user input.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from models.task import TRIAGE_MODES, TriageMode, TriagePlan
from services.gcal_service import GCalService
from services.llm_service import LLMService
from services.todoist_service import TodoistService
from tui import format_duration, render_time_blocks

load_dotenv(override=False)
console = Console()


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


def render_triage_plan(
    plan: TriagePlan,
    schedule_events: list[dict[str, Any]],
    free_blocks: list[Any],
    elapsed_sec: float | None = None,
) -> None:
    """Render a beautiful Rich TUI panel showing schedule and 1-3-5 plan."""
    console.clear()
    console.print("\n[bold cyan]⚡ TaskMaster Triage Helper — Terminal UI[/bold cyan]\n")

    # Schedule table
    sched_table = Table(title="📅 Today's Schedule", expand=True)
    sched_table.add_column("Time / Type", style="dim", width=20)
    sched_table.add_column("Event / Block", style="bold")

    if schedule_events:
        for ev in schedule_events:
            summary = ev.get("summary", "(no title)")
            start = str(GCalService.format_event_time(ev))
            sched_table.add_row(start, f"[yellow]{summary}[/yellow]")
    else:
        sched_table.add_row("-", "[dim]No calendar events today[/dim]")

    if free_blocks:
        for fb in free_blocks:
            when = GCalService.format_time_range(fb.start, fb.end)
            sched_table.add_row(when, "[green]Free time block[/green]")

    # 1-3-5 Plan table
    dur_str = f" · ⏱️ {format_duration(elapsed_sec)}" if elapsed_sec is not None else ""
    plan_table = Table(
        title=(f"🎯 1-3-5 Daily Plan ({plan.quadrant} / {plan.domain} / {plan.mode}){dur_str}"),
        expand=True,
    )
    plan_table.add_column("Category", style="bold cyan", width=14)
    plan_table.add_column("Task Content", style="white")

    for task in plan.big:
        plan_table.add_row("[bold red]BIG (1)[/bold red]", f"[bold]{task.content}[/bold]")
    for task in plan.medium:
        plan_table.add_row("[yellow]MEDIUM (3)[/yellow]", task.content)
    for task in plan.small:
        plan_table.add_row("[green]SMALL (5)[/green]", task.content)
    for task in plan.postponed:
        stale_badge = (
            f" [bold yellow][STALE {task.days_overdue}d][/bold yellow]" if task.is_stale else ""
        )
        plan_table.add_row(
            "[dim magenta]POSTPONED[/dim magenta]", f"[dim]{task.content}[/dim]{stale_badge}"
        )

    console.print(Columns([Panel(sched_table), Panel(plan_table)]))

    if plan.schedule and plan.schedule.slots:
        console.print(Panel(render_time_blocks(plan.schedule)))


def run_cli(
    auto: bool = False,
    json_output: bool = False,
    dry_run: bool = False,
    sync: bool = False,
    backend: str | None = None,
    mode: TriageMode = "balanced",
    time_block: bool = False,
) -> TriagePlan:
    """Run the triage process in interactive or autonomous mode."""
    start_time = time.perf_counter()
    if not auto and not json_output:
        console.print("[dim]Fetching tasks from Todoist and events from Google Calendar...[/dim]")

    # Run static pre-checks before creating services to prevent blocking OAuth consent flow hangs

    credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    gc_ok, gc_msg = GCalService.validate_credentials_static(credentials_path=credentials_path)
    if not gc_ok:
        console.print(f"[bold red]❌ Google Calendar Pre-check Failed: {gc_msg}[/bold red]")
        raise SystemExit(1)

    backends = LLMService.get_available_backends()
    target_cfg = None
    if isinstance(backends, dict) and backends:
        target_cfg = backends.get(backend or "") or next(iter(backends.values()), None)

    if target_cfg:
        llm_ok, llm_msg = LLMService.validate_backend(target_cfg)
        if not llm_ok:
            console.print(f"[bold red]❌ LLM Pre-check Failed: {llm_msg}[/bold red]")
            raise SystemExit(1)

    todoist, gcal, llm = get_services(backend_key=backend)

    td_ok, td_msg = todoist.validate_credentials()
    if not td_ok:
        console.print(f"[bold red]❌ Todoist Pre-check Failed: {td_msg}[/bold red]")
        raise SystemExit(1)

    tasks = todoist.get_todays_tasks()
    events = gcal.get_todays_events()

    tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Helsinki"))
    day_start = datetime.now(tz=tz).replace(hour=8, minute=0, second=0)
    day_end = datetime.now(tz=tz).replace(hour=18, minute=0, second=0)
    free_blocks = gcal.get_free_time_blocks(day_start=day_start, day_end=day_end)

    if not auto and not json_output:
        with console.status(
            f"[bold cyan]Generating {mode} plan with LLM...[/bold cyan]",
            spinner="dots",
        ):
            plan = llm.plan_triage(
                tasks=tasks, free_blocks=free_blocks, mode=mode, time_block=time_block
            )
    else:
        plan = llm.plan_triage(
            tasks=tasks, free_blocks=free_blocks, mode=mode, time_block=time_block
        )

    elapsed_sec = time.perf_counter() - start_time

    if json_output:
        print(plan.model_dump_json(indent=2))
    else:
        render_triage_plan(plan, events, free_blocks, elapsed_sec=elapsed_sec)
        if dry_run:
            console.print(
                "[yellow]ℹ DRY RUN / Read-Only Mode: "
                "Plan generated, no changes synced to Todoist.[/yellow]"
            )
        elif sync:
            tomorrow = datetime.now().date() + timedelta(days=1)
            todoist.sync_plan_tags(plan, tomorrow=tomorrow)
            console.print("[green]✔ Plan tags synced to Todoist.[/green]")

        if auto:
            console.print("[green]✔ Autonomous morning triage completed successfully.[/green]")

    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="TaskMaster TUI & Autonomous CLI")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run autonomously without user input (for cron/launchd)",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON plan")
    parser.add_argument(
        "--cli", action="store_true", help="Run quick non-interactive Rich panel render"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run triage without making any remote changes or syncing to Todoist",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Automatically sync proposed 1-3-5 plan priorities back to Todoist",
    )
    parser.add_argument(
        "--backend",
        type=str,
        default=None,
        help="LLM backend key defined in .env (e.g. 'local', 'gemini')",
    )
    parser.add_argument(
        "--mode",
        choices=TRIAGE_MODES,
        default="balanced",
        help="Triage mode shaping how the LLM picks today's 1-3-5 tasks",
    )
    parser.add_argument(
        "--time-block",
        action="store_true",
        help="Run pass 2: fit the selected tasks into today's free calendar blocks",
    )
    args = parser.parse_args()

    if args.auto or args.json or args.cli or args.dry_run:
        run_cli(
            auto=args.auto,
            json_output=args.json,
            dry_run=args.dry_run,
            sync=args.sync,
            backend=args.backend,
            mode=args.mode,
            time_block=args.time_block,
        )
    else:
        from tui import TaskMasterApp

        app = TaskMasterApp()
        app.run()


if __name__ == "__main__":
    main()
