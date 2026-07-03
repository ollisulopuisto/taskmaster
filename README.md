# TaskMaster Triage Helper

A local-first daily triage assistant. Pulls tasks from Todoist and free-time blocks
from Google Calendar, then uses a local LLM (llama-server / Ollama) to propose a
1-3-5 daily plan (1 big, 3 medium, 5 small tasks) sorted by Eisenhower quadrant
and domain.

## Stack

- Python, `uv` package manager
- FastAPI backend (port **8002**)
- Streamlit frontend (V1)
- Local LLM via llama-server (OpenAI-compatible `/v1` API on port **8000**)
  or Ollama (default port 11434) + `litellm` / `instructor` (structured JSON output)
- `pytest` + `pytest-mock` for tests (external APIs are mocked)
- `ruff` for linting and formatting

## Port map (this machine)

| Service            | URL                              | Notes                                  |
|--------------------|----------------------------------|----------------------------------------|
| llama-server (LLM) | `http://localhost:8000/v1`        | serves `gemma-4-12b-it-Q4_K_M.gguf`    |
| omacrm              | `http://localhost:8001`          | sibling project — keep separate        |
| TaskMaster FastAPI  | `http://localhost:8002`          | `--port 8002`                          |
| Streamlit           | `http://localhost:8501`          | default Streamlit port                 |

If you already have a service on :8000 or :8001, keep it there and run
TaskMaster on :8002. The LLM URL is configured via `OLLAMA_BASE_URL` in `.env`.

## Setup

```bash
uv sync
cp .env.example .env
# fill in TODOIST_API_TOKEN and Google OAuth credentials
# set OLLAMA_BASE_URL + OLLAMA_MODEL to match your local LLM server
```

## Run

```bash
# Backend (port 8002 to avoid colliding with llama-server on :8000 and omacrm on :8001)
uv run uvicorn main:app --reload --pythonpath src --port 8002

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
