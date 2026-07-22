# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Calendar Versioning](https://calver.org/) (`vYY.MM.DD.N`).

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
