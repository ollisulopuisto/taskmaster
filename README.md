# TaskMaster Triage Helper

A local-first daily triage assistant. Pulls tasks from Todoist and free-time blocks
from Google Calendar, then uses a local LLM (Ollama) to propose a 1-3-5 daily plan
(1 big, 3 medium, 5 small tasks) sorted by Eisenhower quadrant and domain.

## Stack

- Python, `uv` package manager
- FastAPI backend
- Streamlit frontend (V1)
- Local LLM via Ollama + `litellm` / `instructor` (structured JSON output)
- `pytest` + `pytest-mock` for tests (external APIs are mocked)
- `ruff` for linting and formatting

## Setup

```bash
uv sync
cp .env.example .env
# fill in TODOIST_API_TOKEN and Google OAuth credentials
```

## Run

```bash
# Backend
uv run uvicorn main:app --reload --pythonpath src

# Frontend (V1)
uv run streamlit run app.py
```

## Test

```bash
uv run pytest
uv run ruff format . && uv run ruff check . --fix
```

## Workflow

Strict TDD per feature: Red → Green → lint → commit. Version tags use CalVer
(`vYY.MM.DD.N`). Never commit secrets — `.env`, `credentials.json`, and
`token.json` are gitignored.
