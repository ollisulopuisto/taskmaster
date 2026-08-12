# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Calendar Versioning](https://calver.org/) (`vYY.MM.DD.N`).

## [v26.08.12.55] - 2026-08-12

### Fixed

- **gcal**: Update `_anchor()` path resolution to check `Path.cwd()` before `_PROJECT_ROOT`, ensuring `credentials.json` is found when running from directories linked or launched across different machines/paths.

## [v26.08.12.54] - 2026-08-12

### Changed

- **oauth**: Replace the manual code-paste `AuthModal` flow with a fully automatic local loopback callback server (`start_local_auth_server()`). A one-shot HTTP server binds on a random free port, the consent URL is opened in the browser, and the authorization code is captured automatically when Google redirects back — no copy-paste required.
- **gcal**: Add `start_local_auth_server()` method to `GCalService`; anchored `credentials.json` / `token.json` paths to the project root via `_anchor()` so relative paths work from any working directory on any machine.
- **tests**: Rewrite OAuth TUI tests to mock `start_local_auth_server` (4-tuple API); add `_auth_timeout` parameter to `action_auth_gcal` for deterministic timeout testing.

## [v26.08.12.53] - 2026-08-12

### Added

- **llm**: Discover LLM servers on the LAN via mDNS/ZeroConf (`discover_lan_backends`); browsing `_ollama._tcp.local.`, `_llm._tcp.local.`, `_openai._tcp.local.` with a 120 s TTL cache.
- **llm**: Opt-in subnet scan (`discover_subnet_backends`) probing LAN_LLM_PORTS across the local `/24` subnet.
- **llm**: Local port probing (`discover_local_backends`) checks ports 8000, 11434, 1234 on startup.

## [v26.08.12.52] - 2026-08-12


### Added

- **tui**: Add in-app interactive Google Calendar OAuth flow (`AuthModal` dialog, `action_auth_gcal`, keyboard binding `a`, and top-bar button `🔑 OAuth GCal (A)`). Users can now complete the Google Calendar OAuth consent flow directly inside the application, obtain the auth URL, and enter the authorization code to save `token.json` without leaving the TUI.

## [v26.08.12.51] - 2026-08-12

### Fixed

- **gcal**: Make `GCalService._service` initialization lazy so instantiating `GCalService` never triggers blocking OAuth consent flow (`flow.run_local_server()`) during `__init__`.
- **tui**: Perform static GCal credential pre-checks in `action_generate_plan()` and `action_fetch_debug()` prior to service instantiation, preventing terminal raw mode freezes on missing/expired credentials.
- **tests**: Fix `test_from_env_reads_gemini_vars` and `test_from_env_falls_back_to_ollama_vars` by clearing `LLM_DEFAULT_BACKEND` in environment monkeypatch.

## [v26.08.12.50] - 2026-08-12

### Fixed

- **cli**: Add static GCal credential pre-checks in `run_cli()` prior to instantiating services to prevent silent hanging on `InstalledAppFlow.run_local_server()` when `token.json` is missing or expired.

## [v26.08.12.49] - 2026-08-12


### Fixed

- **tui**: Add live ticking progress loading timer (`⏳ (Xs elapsed)`) during LLM generation and 120s request timeout protection to prevent TUI hanging without visual feedback.
- **tui**: Add GCal and Todoist credential pre-checks to `action_fetch_debug()` to prevent blocking OAuth consent flow hangs.
- **tui**: Wrap all async TUI actions (`sync_plan`, `submit_debrief`, `fetch_debug`, `generate_plan`) in exception handlers to display explicit error messages directly in UI.

## [v26.08.12.48] - 2026-08-12


### Added

- **services**: Add `LLMService.save_settings_to_env()` to persist default LLM backend selection and autodiscovered backend definitions into `.env`.
- **tui**: Add "💾 Save Settings (V)" action button and key binding (`v`) to Textual TUI top bar to persist selected LLM configurations to `.env`.

## [v26.08.12.47] - 2026-08-12


### Added

- **services**: Add lightweight API pre-check methods (`TodoistService.validate_credentials()`, `GCalService.validate_credentials()`, `LLMService.validate_backend()`) to verify tokens, OAuth state, and backend reachability prior to running triage plans.
- **tui**: Integrate service pre-checks into `action_generate_plan()` to halt execution early with explicit failure messages if any token or server is invalid/unreachable.

## [v26.08.12.46] - 2026-08-12

### Fixed

- **tui**: Fix UI status sequence in `action_generate_plan()` to show data ingestion status before LLM prompt execution, and add try-catch error handling to display GCal/Todoist ingestion failures cleanly.

## [v26.08.12.45] - 2026-08-12

### Added

- **llm**: Add local LLM server autodiscovery (`LLMService.discover_local_backends()`) targeting common ports (`8000`, `11434`, `1234`) and standard OpenAI/Ollama endpoints.
- **tui**: Add "🔄 Discover LLMs" action button to Textual TUI top bar to probe active local model servers and dynamically populate the backend selector.

## [v26.08.12.44] - 2026-08-12

### Fixed

- **gcal**: Catch `RefreshError` and token loading errors in `GCalService._load_credentials()` to purge invalid or expired `token.json` files and re-trigger consent flow automatically.

## [v26.08.12.43] - 2026-08-12

### Changed

- **todoist**: Replace priority overwriting with non-destructive label tags (`big`, `medium`, `small`) in `sync_plan_tags()`, preserving intrinsic Todoist priorities across daily triage sessions.

## [v26.08.12.42] - 2026-08-12

### Added

- **todoist**: Exclude locked tasks labeled `lock`, `locked`, or `taskmaster-lock` (case-insensitive) during task ingestion in `TodoistService.get_todays_tasks()`, configurable via `TODOIST_LOCK_LABELS` environment variable.

## [v26.07.23.41] - 2026-07-23

### Fixed

- **cli**: Render postponed tasks (including `[STALE]` badges) and execution duration badge in `render_triage_plan()` for CLI and `--dry-run` output.

## [v26.07.23.40] - 2026-07-23

### Added

- **cli**: Add `--dry-run`, `--sync`, and `--backend <key>` command-line flags to `src/cli.py` for safe batch runs, read-only testing, and automated cron/LaunchAgent triage.

## [v26.07.23.39] - 2026-07-23

### Documentation

- **docs**: Align `.env.example` with current port mapping (FastAPI on 8002), multi-backend configuration, and optional disk caching comments (`LLM_CACHE_DIR`).

## [v26.07.23.38] - 2026-07-23

### Documentation

- **docs**: Comprehensive overhaul of `README.md` and `AGENTS.md` covering Textual TUI & autonomous CLI usage, multi-backend LLM settings, duration tracking, payload caching, and GitHub repository details.

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
