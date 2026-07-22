# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Calendar Versioning](https://calver.org/) (`vYY.MM.DD.N`).

## [v26.07.22.17] - 2026-07-22

### Fixed

- **llm**: Increase HTTP client timeout to 600s in `LLMService` and `app.py` to allow full Gemma 12B reasoning runs (~5–6 minutes) to finish without timing out.

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
