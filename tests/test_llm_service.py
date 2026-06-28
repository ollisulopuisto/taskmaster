"""Tests for the LLM triage service."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from models.task import Task, TriagePlan
from services.llm_service import LLMService


def _task(*, id: str = "1", content: str = "Write report", project_id: str = "proj-1") -> Task:
    return Task(id=id, content=content, project_id=project_id, due_date=date(2026, 6, 28))


class TestLLMService:
    def _service(self) -> LLMService:
        return LLMService(model="llama3", base_url="http://localhost:11434/v1")

    def _fake_instructor(self) -> MagicMock:
        return MagicMock()

    def _patch_instructor(self, fake: MagicMock):
        return patch("services.llm_service.instructor", fake)

    def test_plan_triage_returns_parsed_pydantic_model(self) -> None:
        tasks = [_task(id="1", content="Write report"), _task(id="2", content="Email team")]
        expected = TriagePlan(
            big=[tasks[0]],
            medium=[tasks[1]],
            small=[],
            quadrant="urgent_important",
            domain="work",
        )

        fake_instructor = self._fake_instructor()
        # instructor.from_openai(...).chat.completions.create(...) returns the model
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = expected
        fake_instructor.from_openai.return_value = fake_client

        with self._patch_instructor(fake_instructor):
            result = self._service().plan_triage(tasks=tasks, free_blocks=[])

        assert isinstance(result, TriagePlan)
        assert len(result.big) == 1
        assert result.big[0].content == "Write report"
        assert result.quadrant == "urgent_important"

    def test_plan_triage_sends_prompt_with_task_context(self) -> None:
        tasks = [_task(id="1", content="Write report")]
        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        fake_client.chat.completions.create.return_value = TriagePlan()

        with self._patch_instructor(fake_instructor):
            self._service().plan_triage(tasks=tasks, free_blocks=[])

        # Verify the call included our task content in the messages
        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        prompt_text = " ".join(m.get("content", "") for m in messages)
        assert "Write report" in prompt_text

    def test_plan_triage_sends_free_blocks_in_prompt(self) -> None:
        from models.task import TimeBlock

        tasks = [_task(id="1", content="Write report")]
        blocks = [TimeBlock(start="2026-06-28T09:00:00+00:00", end="2026-06-28T10:00:00+00:00")]

        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        fake_client.chat.completions.create.return_value = TriagePlan()

        with self._patch_instructor(fake_instructor):
            self._service().plan_triage(tasks=tasks, free_blocks=blocks)

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        prompt_text = " ".join(m.get("content", "") for m in messages)
        assert "09:00" in prompt_text

    def test_plan_triage_uses_response_format_model(self) -> None:
        """Verify the LLM is asked to return structured JSON matching TriagePlan."""
        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        fake_client.chat.completions.create.return_value = TriagePlan()

        with self._patch_instructor(fake_instructor):
            self._service().plan_triage(tasks=[], free_blocks=[])

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        # instructor passes the pydantic model as response_model
        assert call_kwargs.get("response_model") is TriagePlan

    def test_plan_triage_handles_empty_task_list(self) -> None:
        fake_instructor = self._fake_instructor()
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = TriagePlan()
        fake_instructor.from_openai.return_value = fake_client

        with self._patch_instructor(fake_instructor):
            result = self._service().plan_triage(tasks=[], free_blocks=[])

        assert isinstance(result, TriagePlan)
        assert result.big == []
        assert result.medium == []
        assert result.small == []
