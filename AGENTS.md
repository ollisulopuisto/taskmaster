# AGENTS.md — TaskMaster Triage Helper

This file captures repo-specific facts that are easy to miss. Prefer it over re-reading `PLAN.md`.

## Project state

This repo is **not yet scaffolded**. There is no `pyproject.toml`, no source code, and no commits — only `PLAN.md`. Before running any command below, the project must be initialized with `uv init` and dependencies installed.

## Stack (from `PLAN.md`)

- **Language:** Python
- **Package manager:** `uv` (not pip/poetry)
- **Backend:** FastAPI + Uvicorn
- **Frontend (V1):** Streamlit (all-Python, preferred); React + Vite + Tailwind is the alternative
- **LLM:** local Ollama via `litellm` or `instructor` (structured JSON output)
- **Testing:** `pytest` + `pytest-mock`
- **Lint/format:** `ruff`

## Commands (only valid after scaffolding)

```bash
uv init
uv add fastapi uvicorn pytest pytest-mock ruff todoist-api-python google-api-python-client litellm pydantic
uv run ruff format .
uv run ruff check . --fix
uv run pytest
```

There is no way to run a single test yet — `tests/` does not exist. Once it does, use `uv run pytest tests/path/test_file.py::test_name`.

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
