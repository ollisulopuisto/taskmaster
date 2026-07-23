# TaskMaster Triage Helper

A local-first, intelligent daily triage assistant for macOS and Linux. TaskMaster pulls tasks from **Todoist** and free-time blocks from **Google Calendar**, then utilizes an LLM (local via `llama-server`/`Ollama` or cloud via Google Gemini / OpenRouter) to automatically propose a structured **1-3-5 daily plan** (1 Big, 3 Medium, 5 Small tasks) categorized by Eisenhower matrix quadrant and life domain.

Repository: [github.com/ollisulopuisto/taskmaster](https://github.com/ollisulopuisto/taskmaster)

---

## ⚡ Key Features

- **Interactive Textual TUI (`src/tui.py`):** Rich terminal UI with full mouse & keyboard navigation, tabs (*Morning Triage*, *Evening Debrief*, *🔍 Debug Data*), dynamic backend selector, and interactive task reassignment buttons.
- **Autonomous CLI (`src/cli.py`):** Non-interactive execution mode (`--auto`, `--json`) designed for automated cron jobs or macOS `LaunchAgent` daemons.
- **Multi-Backend LLM Engine:** Seamlessly switch between local models (`llama-server`, `Ollama`) and cloud LLMs (Google Gemini 3.5 Flash, OpenRouter) via environment configuration and a live TUI dropdown widget.
- **Payload Disk Caching:** SHA-256 deterministic payload hashing (`.cache/triage_<hash>.json`) prevents duplicate LLM invocations for identical task/schedule state.
- **Heuristic Triage & Priority Sync:**
  - Automatic staleness identification (tasks overdue >= 7 days marked as `[STALE]` and routed to `postponed`).
  - Exclusion of recurring tasks during daily ingestion.
  - Interactive task reassignment with automatic capacity overflow handling (demotes displaced tasks).
  - One-click priority synchronization back to Todoist.
  - Evening Debrief rollover to automatically roll uncompleted items to tomorrow.
- **Execution Timing:** Tracks and displays human-readable generation duration (e.g. `⏱️ 4.2s` or `1m 15s`).

---

## 🛠 Stack & Architecture

- **Language & Package Manager:** Python 3.13+, managed with `uv`.
- **UI:** Textual TUI (`src/tui.py`, preferred) & Streamlit Web UI (`app.py`).
- **Backend:** FastAPI + Uvicorn (`src/main.py` on port `8002`).
- **LLM Client:** `instructor` + `OpenAI` client in `instructor.Mode.MD_JSON` with strict Pydantic model validation (`TriagePlanIDs`, `TriagePlan`).
- **Testing & Quality:** `pytest` + `pytest-mock` (all external APIs mocked, 85+ unit tests) and `ruff` formatting/linting.
- **Versioning:** Calendar Versioning (`vYY.MM.DD.N`).

---

## ⚙️ Configuration & Multi-Backend Setup

Copy `.env.example` to `.env` and configure your credentials:

```bash
cp .env.example .env
```

### Multi-Backend LLM Configuration in `.env`

TaskMaster supports multiple configured LLM backends:

```ini
# Multi-backend list (comma-separated keys)
LLM_BACKENDS=local,gemini

# Default backend when app starts
LLM_DEFAULT_BACKEND=local

# Local LLM (llama-server / Ollama)
LLM_BACKEND_LOCAL_NAME="Local Gemma (llama-server)"
LLM_BACKEND_LOCAL_BASE_URL="http://localhost:8000/v1"
LLM_BACKEND_LOCAL_MODEL="gemma-4-12b-it-Q4_K_M.gguf"
LLM_BACKEND_LOCAL_API_KEY="ollama"
LLM_BACKEND_LOCAL_TIMEOUT=600

# Google Gemini API
LLM_BACKEND_GEMINI_NAME="Google Gemini 3.5 Flash Lite"
LLM_BACKEND_GEMINI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
LLM_BACKEND_GEMINI_MODEL="gemini-2.5-flash"
LLM_BACKEND_GEMINI_API_KEY="your-gemini-api-key"
LLM_BACKEND_GEMINI_TIMEOUT=30
```

---

## 🚀 Running TaskMaster

### 1. Interactive Textual TUI (Preferred)

No background FastAPI server required — runs completely self-contained in a single terminal process:

```bash
uv run python src/cli.py
```

#### Keyboard Shortcuts:
- `G` : Generate 1-3-5 plan with selected LLM backend
- `S` : Confirm & sync plan priorities to Todoist
- `Q` : Quit application

### 2. Autonomous CLI (for Cron / LaunchAgent)

```bash
# Run silently and output summary
uv run python src/cli.py --auto

# Output raw JSON plan
uv run python src/cli.py --json
```

### 3. Streamlit Web UI (Optional)

```bash
# Start FastAPI backend (port 8002)
uv run uvicorn main:app --reload --pythonpath src --port 8002

# Start Streamlit frontend
uv run streamlit run app.py
```

---

## 🧪 Testing & Code Quality

```bash
# Run unit test suite (85+ tests, 100% isolated without external network calls)
uv run pytest

# Format and lint codebase
uv run ruff format .
uv run ruff check . --fix
```

---

## 🔒 Security

All secret keys and token artifacts (`.env`, `credentials.json`, `token.json`) are strictly gitignored and excluded from version control.
