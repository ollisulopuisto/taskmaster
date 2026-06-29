# Plan

This plan outlines the specific steps for a junior developer to bridge the gaps in the TaskMaster Triage Helper. The goal is to move the application from mocked endpoints to a fully integrated, local-running system with real external integrations (Todoist, GCal, local LLM).

## Scope

- **In**:
    - Adding `.env` file support and loading secrets/configuration.
    - Implementing a Google OAuth credential verification and storage flow in the calendar service.
    - Implementing the task roll-over (postponing to tomorrow) in the Todoist service and evening debrief endpoint.
    - Exposing GCal schedule/free blocks in the morning endpoint and rendering them in the Streamlit UI.
    - Writing pytest unit tests (using mocks) for all new functionality following Red-Green-Refactor TDD.
- **Out**:
    - Building a multi-user authentication system (only single-user local auth is required).
    - Deploying the app to production/cloud (keep it running on local uvicorn and streamlit).

## Action Items

### Step 1: Configuration & Environment Variables
*   [ ] **Add Dependencies**: Run `uv add python-dotenv google-auth-oauthlib google-auth-httplib2` to install environment parsing and Google OAuth libraries.
*   [ ] **Verify Gitignore**: Ensure `.env`, `credentials.json`, and `token.json` are listed in [.gitignore](file:///Users/dst/Documents/koodi/taskmaster/.gitignore) to prevent committing sensitive keys.
*   [ ] **Load Settings**: Implement environment loading inside [router.py](file:///Users/dst/Documents/koodi/taskmaster/src/api/router.py). Replace the hardcoded placeholders in `_build_dependencies()` with dynamic reads from `os.getenv` (e.g. `TODOIST_API_TOKEN`, `OLLAMA_MODEL`, `OLLAMA_BASE_URL`).

### Step 2: Google Calendar OAuth Integration
*   [ ] **Implement Credentials Flow**: Update [gcal_service.py](file:///Users/dst/Documents/koodi/taskmaster/src/services/gcal_service.py). Write an authentication helper that:
    1. Looks for a stored credential file `token.json`.
    2. If missing or invalid, reads from `GOOGLE_CREDENTIALS_PATH` (defaulting to `credentials.json`) and triggers the local desktop OAuth consent browser flow via `InstalledAppFlow.from_client_secrets_file`.
    3. Saves the authorized credentials back to `token.json` for future sessions.
    4. Passes the credentials object to `build("calendar", "v3", credentials=creds)`.
*   [ ] **Mock Authentication in Tests**: Update [test_gcal_service.py](file:///Users/dst/Documents/koodi/taskmaster/tests/test_gcal_service.py) to mock out the OAuth library calls and ensure the unit test suite does not trigger real web flows or fail due to missing local tokens.

### Step 3: Implement Task Postponing / Roll-Over
*   [ ] **Add Service Method**: Add `postpone_task(self, task_id: str, new_date: date)` to [todoist_service.py](file:///Users/dst/Documents/koodi/taskmaster/src/services/todoist_service.py). Under the hood, this should invoke `self._api.update_task(task_id=task_id, due_date=new_date.isoformat())`.
*   [ ] **Update Backend Handler**: Modify the `/evening` POST endpoint in [router.py](file:///Users/dst/Documents/koodi/taskmaster/src/api/router.py) to loop through `payload.rolled_over_ids` and call the service to postpone them to tomorrow's date.
*   [ ] **Write Tests First**: In [test_todoist_service.py](file:///Users/dst/Documents/koodi/taskmaster/tests/test_todoist_service.py) and [test_api.py](file:///Users/dst/Documents/koodi/taskmaster/tests/test_api.py), write tests that assert that `postpone_task` triggers the correct Todoist API payload and is called with tomorrow's date for every rolled-over item.

### Step 4: Expose & Display Calendar Schedule
*   [ ] **Update Morning Response**: Update the FastAPI `/morning` endpoint in [router.py](file:///Users/dst/Documents/koodi/taskmaster/src/api/router.py) to return a dict containing both `plan` (the Pydantic `TriagePlan`) and `schedule` (a list of today's busy events and free blocks).
*   [ ] **Render in UI**: Edit [app.py](file:///Users/dst/Documents/koodi/taskmaster/app.py). Render a two-column layout in the "Morning Triage" tab using `st.columns([1, 2])`. Show a visual list of calendar events and free slots in the left column, and the 1-3-5 plan with checkable tasks in the right column.
*   [ ] **Verify UI State**: Ensure Streamlit stores both the plan and the schedule in `st.session_state` so the schedule remains visible after interactions.

### Step 5: Validation & Formatting
*   [ ] **Run Test Suite**: Run `uv run pytest` to ensure all existing and new mocks verify correctly.
*   [ ] **Lint & Format**: Run `uv run ruff format .` and `uv run ruff check . --fix` to verify style guidelines.

## Open Questions

1. **How should calendar IDs be supplied from the environment?**
   * *Assumption*: Read `GOOGLE_CALENDAR_IDS` from `.env`. Split it by comma (e.g. `"primary,work@example.com"`) and construct a list.
2. **What time window should be scanned for calendar free-busy checks?**
   * *Assumption*: Standard working hours: `08:00` to `18:00` in the user's local timezone.
