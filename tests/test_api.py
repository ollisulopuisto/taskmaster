"""Tests for the triage API endpoints."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from models.task import TriagePlan


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
        assert body["quadrant"] == "urgent_important"
        assert body["domain"] == "work"
        assert "big" in body
        assert "medium" in body
        assert "small" in body

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
