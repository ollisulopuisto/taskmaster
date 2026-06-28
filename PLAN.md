# TaskMaster Triage Helper - Implementation Plan

This document outlines the detailed, step-by-step implementation plan for building the TaskMaster Triage helper. It is designed to be executed by a junior developer or a coding assistant LLM.

## 1. Technology Stack & Tooling

Based on modern best practices and user preferences, use the following stack:

*   **Package Management:** `uv` (Fast Python package and workspace manager).
*   **Backend Framework:** Python with **FastAPI**. It's fast, modern, and excellent for building API endpoints that a frontend can consume.
*   **Frontend:** A lightweight **Streamlit** app or a **React (Vite) + Tailwind CSS** SPA. For V1, Streamlit is recommended as it minimizes context switching (all Python) and handles LLM chat/data display effortlessly.
*   **Local LLM Integration:** `ollama` (running locally) combined with the `litellm` or `instructor` Python libraries to enforce structured JSON outputs from the LLM (crucial for getting consistent 1-3-5 categorizations).
*   **Testing Suite:** `pytest` (with `pytest-mock` for mocking Todoist/GCal API calls).
*   **Linting & Formatting:** `ruff` (fast Python linter and formatter).

---

## 2. The Development Workflow (Strict TDD)

You must follow a strict **Red-Green-Refactor TDD workflow** for every feature:

1.  **Red (Test First):** Write a `pytest` test for the specific function or API endpoint you are about to build. Run it to ensure it fails.
2.  **Green (Implement):** Write the *minimal* amount of code required to make the test pass. Do not over-engineer.
3.  **Refactor & Lint:** Clean up the code. **Crucially, you must run the following before committing any code to ensure CI compliance:**
    ```bash
    uv run ruff format .
    uv run ruff check . --fix
    ```
4.  **Commit:** Commit your changes using CalVer (vYY.MM.DD.N) in the commit message if applicable, and ensure minor bugs are just committed (no PR needed).

---

## 3. Step-by-Step Implementation Guide

### Phase 1: Project Scaffolding
1.  Initialize the Python project using `uv init`.
2.  Install core dependencies: `uv add fastapi uvicorn pytest pytest-mock ruff todoist-api-python google-api-python-client litellm pydantic`.
3.  Set up the folder structure:
    ```text
    ├── src/
    │   ├── api/          # FastAPI routes
    │   ├── services/     # Todoist, GCal, LLM logic
    │   ├── models/       # Pydantic data schemas
    │   └── main.py       # FastAPI app entrypoint
    ├── tests/            # Pytest test files
    ├── pyproject.toml    # uv and ruff configuration
    └── .env.example      # Example environment variables
    ```
4.  Configure `ruff` in `pyproject.toml` to match standard Python guidelines.

### Phase 2: Data Ingestion Services (TDD)
*Use mock data in your tests to avoid hitting real APIs during development.*

1.  **Todoist Service:**
    *   *Test:* Write a test that mocks `TodoistAPI.get_tasks` and verifies your service returns a standardized list of internal `Task` Pydantic models.
    *   *Implement:* Build `src/services/todoist_service.py`. Ensure it only fetches tasks that are due today or overdue.
2.  **Google Calendar Service:**
    *   *Test:* Write a test that mocks the Google Calendar API and verifies your service returns a list of today's events (start time, end time, summary) and calculates available free time blocks.
    *   *Implement:* Build `src/services/gcal_service.py`. Support querying multiple calendar IDs (Work, Personal, Family).

### Phase 3: The LLM Brain (TDD)
This is the core logic engine. We need the local LLM to return *structured data* so our app can render it properly.

1.  **Define Pydantic Models:**
    *   Create a schema for the LLM output. It should include categories for the 1 Big, 3 Medium, and 5 Small tasks, plus their assigned Eisenhower quadrant (Urgent/Important) and Domain (Work/Civilian/Family).
2.  **LLM Service:**
    *   *Test:* Mock the LLM API call. Assert that your service parses the JSON response into your Pydantic models correctly.
    *   *Implement:* Build `src/services/llm_service.py`. Construct a prompt that injects the fetched Todoist tasks and GCal free blocks. Send this to the local LLM (via Ollama) and request the response in JSON matching your schema. 

### Phase 4: API Endpoints (TDD)
Expose the services via FastAPI.

1.  **GET `/api/triage/morning`:** 
    *   *Action:* Calls Todoist and GCal services, passes data to the LLM service, and returns the proposed 1-3-5 plan.
2.  **POST `/api/triage/evening`:**
    *   *Action:* Accepts a list of completed/rolled-over tasks, updates Todoist accordingly, and logs completion stats.

### Phase 5: The User Interface
If using Streamlit (recommended for MVP):
1.  Create `app.py` in the root.
2.  Build a "Morning Triage" tab that hits the FastAPI backend (or calls services directly), displays the GCal schedule visually, and lists the LLM's proposed 1-3-5 plan with checkboxes.
3.  Build an "Evening Debrief" tab that allows the user to check off what actually got done, moving incomplete items to tomorrow via the Todoist API.

---

## 4. Security & Environment Rules
*   **NEVER COMMIT SECRETS:** Add `.env` to `.gitignore` immediately. API keys for Todoist and GCal must only live in `.env`.
*   Verify that `credentials.json` (Google OAuth) or `token.json` are also gitignored.
