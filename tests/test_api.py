"""Tests for the triage API endpoints."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.router import _build_dependencies
from main import app
from models.task import TriagePlan


class TestBuildDependencies:
    """Verify that service construction reads live values from the environment."""

    def test_uses_env_token_for_todoist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TODOIST_API_TOKEN", "secret-from-env")
        monkeypatch.delenv("GOOGLE_CALENDAR_IDS", raising=False)
        monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        with (
            patch("api.router.TodoistService") as MockTodoist,
            patch("api.router.GCalService"),
            patch("api.router.LLMService"),
        ):
            _build_dependencies()

        MockTodoist.assert_called_once_with(token="secret-from-env")

    def test_splits_comma_separated_calendar_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CALENDAR_IDS", "primary,work@example.com")
        monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
        monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        with (
            patch("api.router.GCalService") as MockGCal,
            patch("api.router.TodoistService"),
            patch("api.router.LLMService"),
        ):
            _build_dependencies()

        MockGCal.assert_called_once_with(
            calendar_ids=["primary", "work@example.com"],
            credentials_path="credentials.json",
        )

    def test_uses_env_ollama_model_and_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_MODEL", "mistral")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:11434/v1")
        monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
        monkeypatch.delenv("GOOGLE_CALENDAR_IDS", raising=False)
        monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)

        with (
            patch("api.router.LLMService") as MockLLM,
            patch("api.router.TodoistService"),
            patch("api.router.GCalService"),
        ):
            _build_dependencies()

        MockLLM.assert_called_once_with(model="mistral", base_url="http://ollama.local:11434/v1")

    def test_uses_env_credentials_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "/tmp/my-oauth.json")
        monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
        monkeypatch.delenv("GOOGLE_CALENDAR_IDS", raising=False)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        with (
            patch("api.router.GCalService") as MockGCal,
            patch("api.router.TodoistService"),
            patch("api.router.LLMService"),
        ):
            _build_dependencies()

        MockGCal.assert_called_once_with(
            calendar_ids=["primary"],
            credentials_path="/tmp/my-oauth.json",
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestMorningEndpoint:
    def test_morning_returns_triage_plan(self, client: TestClient) -> None:
        fake_plan = TriagePlan(
            big=[],
            medium=[],
            small=[],
            quadrant="urgent_important",
            domain="work",
        )

        with (
            patch("api.router.TodoistService") as MockTodoist,
            patch("api.router.GCalService") as MockGCal,
            patch("api.router.LLMService") as MockLLM,
        ):
            MockTodoist.return_value.get_todays_tasks.return_value = []
            MockGCal.return_value.get_todays_events.return_value = []
            MockGCal.return_value.get_free_time_blocks.return_value = []
            MockLLM.return_value.plan_triage.return_value = fake_plan

            response = client.get("/api/triage/morning")

        assert response.status_code == 200
        body = response.json()
        plan = body["plan"]
        assert plan["quadrant"] == "urgent_important"
        assert plan["domain"] == "work"
        assert "big" in plan
        assert "medium" in plan
        assert "small" in plan

    def test_morning_passes_tasks_to_llm(self, client: TestClient) -> None:
        from models.task import Task

        tasks = [
            Task(id="1", content="Write report", project_id="proj-1", due_date=date(2026, 6, 28))
        ]

        with (
            patch("api.router.TodoistService") as MockTodoist,
            patch("api.router.GCalService") as MockGCal,
            patch("api.router.LLMService") as MockLLM,
        ):
            MockTodoist.return_value.get_todays_tasks.return_value = tasks
            MockGCal.return_value.get_todays_events.return_value = []
            MockGCal.return_value.get_free_time_blocks.return_value = []
            MockLLM.return_value.plan_triage.return_value = TriagePlan()

            client.get("/api/triage/morning")

        # Verify the LLM received the tasks
        call_kwargs = MockLLM.return_value.plan_triage.call_args.kwargs
        assert len(call_kwargs["tasks"]) == 1
        assert call_kwargs["tasks"][0].content == "Write report"

    def test_morning_response_includes_schedule(self, client: TestClient) -> None:
        """Morning response must bundle both the plan and the calendar schedule."""
        from models.task import TimeBlock

        fake_plan = TriagePlan(big=[], medium=[], small=[], quadrant="neither", domain="family")
        fake_events = [
            {"id": "1", "summary": "Standup"},
            {"id": "2", "summary": "Lunch"},
        ]
        fake_blocks = [
            TimeBlock(start="2026-06-28T08:00:00+00:00", end="2026-06-28T09:00:00+00:00")
        ]

        with (
            patch("api.router.TodoistService") as MockTodoist,
            patch("api.router.GCalService") as MockGCal,
            patch("api.router.LLMService") as MockLLM,
        ):
            MockTodoist.return_value.get_todays_tasks.return_value = []
            MockGCal.return_value.get_todays_events.return_value = fake_events
            MockGCal.return_value.get_free_time_blocks.return_value = fake_blocks
            MockLLM.return_value.plan_triage.return_value = fake_plan

            response = client.get("/api/triage/morning")

        assert response.status_code == 200
        body = response.json()
        assert "plan" in body
        assert "schedule" in body
        assert body["plan"]["quadrant"] == "neither"
        assert body["plan"]["domain"] == "family"
        assert body["schedule"]["events"] == fake_events
        assert body["schedule"]["free_blocks"] == [
            {"start": "2026-06-28T08:00:00+00:00", "end": "2026-06-28T09:00:00+00:00"}
        ]


class TestEveningEndpoint:
    def test_evening_accepts_completed_tasks(self, client: TestClient) -> None:
        with (
            patch("api.router.TodoistService"),
            patch("api.router.GCalService"),
            patch("api.router.LLMService"),
        ):
            response = client.post(
                "/api/triage/evening",
                json={"completed_ids": ["1", "2"], "rolled_over_ids": ["3"]},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["completed_count"] == 2
        assert body["rolled_over_count"] == 1

    def test_evening_calls_todoist_complete(self, client: TestClient) -> None:
        with (
            patch("api.router.TodoistService") as MockTodoist,
            patch("api.router.GCalService"),
            patch("api.router.LLMService"),
        ):
            svc = MockTodoist.return_value
            client.post(
                "/api/triage/evening",
                json={"completed_ids": ["1", "2"], "rolled_over_ids": []},
            )

        assert svc.complete_task.call_count == 2

    def test_evening_validates_empty_body(self, client: TestClient) -> None:
        with (
            patch("api.router.TodoistService"),
            patch("api.router.GCalService"),
            patch("api.router.LLMService"),
        ):
            response = client.post("/api/triage/evening", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["completed_count"] == 0
        assert body["rolled_over_count"] == 0

    def test_evening_calls_postpone_for_each_rolled_over(self, client: TestClient) -> None:
        """Every rolled-over task triggers postpone_task."""
        with (
            patch("api.router.TodoistService") as MockTodoist,
            patch("api.router.GCalService"),
            patch("api.router.LLMService"),
        ):
            svc = MockTodoist.return_value
            client.post(
                "/api/triage/evening",
                json={"completed_ids": [], "rolled_over_ids": ["1", "2"]},
            )

        assert svc.postpone_task.call_count == 2
        ids_called = {call.args[0] for call in svc.postpone_task.call_args_list}
        assert ids_called == {"1", "2"}

    def test_evening_calls_postpone_with_tomorrow_date(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The date passed to postpone_task should be tomorrow."""
        from datetime import date, timedelta

        called_with: list[date] = []

        def fake_postpone(task_id: str, new_date: date) -> None:
            called_with.append(new_date)

        tomorrow = date.today() + timedelta(days=1)

        with (
            patch("api.router.TodoistService") as MockTodoist,
            patch("api.router.GCalService"),
            patch("api.router.LLMService"),
        ):
            svc = MockTodoist.return_value
            svc.postpone_task.side_effect = fake_postpone
            client.post(
                "/api/triage/evening",
                json={"completed_ids": ["42"], "rolled_over_ids": ["1"]},
            )

        assert called_with == [tomorrow]
        assert svc.complete_task.call_count == 1
