# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Calendar Versioning](https://calver.org/) (`vYY.MM.DD.N`).

## [v26.07.23.37] - 2026-07-23

### Changed

- **tui**: Format plan generation duration into human-readable minutes and seconds (e.g. `45.8s` or `2m 15s`) using `format_duration()` helper.

## [v26.07.23.36] - 2026-07-23

### Added

- **tui**: Track and display plan generation execution duration (e.g. `⏱️ 3.42s`) in the TUI daily plan header and evening debrief summary.

## [v26.07.23.35] - 2026-07-23

### Fixed

- **llm**: Increase default `max_tokens` from 4096 to 8192 and instruct system prompt to omit verbose chain-of-thought reasoning, preventing `IncompleteOutputException` when using local reasoning models (such as Gemma/DeepSeek).
- **tui**: Wrap plan generation in a `try...except` block to cleanly display LLM errors on screen without crashing the Textual UI.

## [v26.07.23.34] - 2026-07-23

### Added

- **tui & llm**: Support multi-backend LLM configuration in `.env` (`LLM_BACKENDS`, `LLM_DEFAULT_BACKEND`, `LLM_BACKEND_<KEY>_*`). Add interactive backend `Select` dropdown widget to the Textual TUI top bar, enabling dynamic LLM switching (e.g. Local Gemma vs Google Gemini 3.5 Flash Lite) with `local` as default out of the box.

## [v26.07.23.33] - 2026-07-23

### Performance

- **llm**: Optimize LLM triage output schema (`TriagePlanIDs`) to return only task ID lists per category instead of entire duplicated `Task` objects. Reduces output tokens by ~40x (from ~2000 to ~50 tokens), preventing local LLM timeouts/formatting bottlenecks while speeding up responses for both local models and cloud APIs (Gemini/OpenAI).

## [v26.07.23.32] - 2026-07-23

### Fixed

- **tui**: Wrap synchronous network I/O and LLM service calls (`todoist`, `gcal`, `llm`) in `asyncio.to_thread` in Textual TUI action handlers to prevent UI thread freezing.

## [v26.07.23.31] - 2026-07-23

### Changed

- **todoist**: Exclude recurring tasks (`raw.due.is_recurring == True`) during task ingestion in `TodoistService.get_todays_tasks()`.

## [v26.07.22.30] - 2026-07-22

### Added

- **tui**: Wire `Evening Debrief` tab to process today's tasks against Todoist API, rolling over any incomplete tasks to tomorrow.

## [v26.07.22.29] - 2026-07-22

### Fixed

- **gcal**: Attach UTC timezone info to offset-naive datetimes (such as all-day events `"YYYY-MM-DD"`) in `GCalService._parse_dt()`, preventing `TypeError: can't compare offset-naive and offset-aware datetimes` during free busy calculations.

## [v26.07.22.28] - 2026-07-22

### Fixed

- **todoist**: Pass `datetime.date` object instead of ISO string to `TodoistAPI.update_task(due_date=...)` in `postpone_task()`, fixing `AttributeError: 'str' object has no attribute 'isoformat'` during sync.

## [v26.07.22.27] - 2026-07-22

### Added

- **tui**: Dynamic task state button styling. The current assignment button for each task row displays a checkmark label (`✓ BIG`, `✓ MED`, `✓ SMALL`, `✓ POST`) with active color variants, while other category buttons remain standard actions.

## [v26.07.22.26] - 2026-07-22

### Fixed

- **tui**: Fix CSS content alignment and refine top bar layout so action buttons and task rows render side-by-side cleanly with keyboard shortcut hints (`G` to Generate, `S` to Sync, `Q` to Quit).

## [v26.07.22.25] - 2026-07-22

### Added

- **tui**: Add interactive task reassignment buttons (`[1 Big]`, `[3 Med]`, `[5 Small]`, `[P Postpone]`) for every task row in `src/tui.py`. Allows instant in-app plan customization with zero LLM API calls.

## [v26.07.22.24] - 2026-07-22

### Added

- **llm**: Add SHA-256 payload disk caching to `LLMService` to eliminate redundant API calls for identical daily task/event inputs (0s latency, $0 cost on cache hits). Add `force_refresh` parameter to bypass cache when needed.

## [v26.07.22.23] - 2026-07-22

### Added

- **triage**: Implement 1-3 day recency window and 7-day staleness threshold heuristics across `Task.is_stale`, `LLMService` prompt rules, and `src/tui.py` stale badge styling.

## [v26.07.22.22] - 2026-07-22

### Added

- **models**: Add `duration_minutes`, `is_overdue`, `days_overdue` to `Task`, add `postponed` list to `TriagePlan`, and implement pure Python `reassign_task` for instant local reordering and capacity enforcement.
- **services**: Parse duration & overdue metadata in `TodoistService`, format duration & overdue info in `LLMService` prompt, and add `sync_plan_priorities` to sync P1/P2/P3 priorities and postpone unassigned tasks back to Todoist.
- **tui**: Render proposed postponed tasks and add `💾 Confirm & Sync to Todoist` button in `src/tui.py`.

## [v26.07.22.21] - 2026-07-22

### Fixed

- **cli**: Fix `from tui import TaskMasterApp` import in `src/cli.py` to prevent `ModuleNotFoundError` when running `uv run src/cli.py`.

## [v26.07.22.20] - 2026-07-22

### Changed

- **llm**: Clean up `.env.example` and tests to focus purely on local LLM vs direct Google Gemini API without any third-party services.

## [v26.07.22.19] - 2026-07-22

### Added

- **llm**: Add `LLMService.from_env()` unified constructor supporting local LLMs (llama-server / Ollama), OpenRouter (Google Gemini, Claude, DeepSeek), or direct Google Gemini OpenAI endpoints without code duplication.

## [v26.07.22.18] - 2026-07-22

### Added

- **tui**: Add full interactive Textual terminal application (`src/tui.py`) with mouse & keyboard navigation, tabs (Morning Triage, Evening Debrief, Debug Data), reactive buttons, and side-by-side Rich schedule panels.
- **cli**: Set `src/tui.py` as default interactive runner for `uv run src/cli.py`.

## [v26.06.29.4] - 2026-06-29

### Added

- **ui**: Render calendar schedule alongside plan (e8dafdc)
- **ui**: Add Streamlit frontend with tested data layer (e2a9bf4)
- **api**: Add FastAPI triage endpoints with TDD (ccb81c8)
- **llm**: Add structured LLM triage service with TDD (7c70aff)
- **gcal**: Add Google Calendar service with TDD (4873ba6)
- **gcal**: Add InstalledAppFlow OAuth credential resolution (48b0e43)
- **todoist**: Add Todoist ingestion service with TDD (5aaa059)
- **todoist**: Add postpone_task for evening roll-over (84d2537)
- **config**: Load service settings from .env (e2a9bf4)

### Infrastructure

- Scaffold project with uv, FastAPI, and ruff config (25df523)

[Unreleased]: https://github.com/dst/taskmaster/compare/v26.06.29.4...HEAD
[v26.06.29.4]: https://github.com/dst/taskmaster/releases/tag/v26.06.29.4
