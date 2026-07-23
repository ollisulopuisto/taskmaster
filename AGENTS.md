# AGENTS.md — TaskMaster Triage Helper

This file captures repo-specific facts that are easy to miss. Prefer it over re-reading `PLAN.md`.

## Project state

This repo is **fully scaffolded and functional**. It features a Textual TUI, autonomous CLI, multi-backend LLM support, disk caching, and comprehensive unit tests.

## Stack

- **Language:** Python 3.13+
- **Package manager:** `uv`
- **UI:** Textual TUI (`src/tui.py`, preferred) & Streamlit Web UI (`app.py`)
- **Backend:** FastAPI + Uvicorn (`src/main.py`)
- **LLM Integration:** OpenAI-compatible API (llama-server, Ollama, Google Gemini, OpenRouter) via `instructor` + Pydantic models
- **Testing:** `pytest` + `pytest-mock`
- **Lint/format:** `ruff`

## Commands

```bash
# Run interactive Textual TUI (preferred)
uv run python src/cli.py

# Run autonomous CLI (for cron / LaunchAgent)
uv run python src/cli.py --auto

# Run single test or full suite
uv run pytest
uv run pytest tests/test_tui.py::test_tui_initial_render

# Formatting & linting
uv run ruff format .
uv run ruff check . --fix
```

## Required workflow order

Every feature follows strict TDD. Do not skip steps:

1. **Red** — write a failing `pytest` test
2. **Green** — write minimal code to pass
3. **Refactor + lint** — run `uv run ruff format . && uv run ruff check . --fix`
4. **Commit** — use CalVer tag `vYY.MM.DD.N` in the message

## Directory layout (planned)

```
src/
├── api/          # FastAPI routes
├── services/     # todoist_service, gcal_service, llm_service
├── models/       # Pydantic schemas
└── main.py       # FastAPI app entrypoint
tests/
pyproject.toml
.env.example
```

## Key conventions that differ from defaults

- **CalVer, not SemVer** — version tags are `vYY.MM.DD.N`
- **All external APIs must be mocked in tests** — never hit real Todoist/Google Calendar during development. No credentials are required to run the test suite.
- **Secrets live only in `.env`** — `.env`, `credentials.json`, and `token.json` must be gitignored before any commit.
- **LLM output must be structured JSON** — use `instructor` or `litellm` with Pydantic models; do not parse free-text LLM responses.
- **Single source of truth for stack/commands:** `PLAN.md`. If this file and `PLAN.md` disagree, `PLAN.md` wins until the project is scaffolded and config files exist.

## What to do first in a fresh session

1. Check whether `pyproject.toml` exists. If not, the repo is uninitialized — scaffold it from `PLAN.md` before writing application code.
2. If scaffolding is needed, create `.gitignore` (including `.env`, `credentials.json`, `token.json`) **before** any `uv add` that might pull OAuth artifacts.
