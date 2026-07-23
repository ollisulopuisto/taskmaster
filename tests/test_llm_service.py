"""Tests for the LLM triage service."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from models.task import Task, TriagePlan, TriagePlanIDs
from services.llm_service import LLMService


def _task(*, id: str = "1", content: str = "Write report", project_id: str = "proj-1") -> Task:
    return Task(id=id, content=content, project_id=project_id, due_date=date(2026, 6, 28))


class TestLLMService:
    def _service(self) -> LLMService:
        return LLMService(
            model="gemma-4-12b-it-Q4_K_M.gguf",
            base_url="http://localhost:8000/v1",
            cache_dir="",
        )

    def _fake_instructor(self) -> MagicMock:
        return MagicMock()

    def _patch_instructor(self, fake: MagicMock):
        return patch("services.llm_service.instructor", fake)

    def test_plan_triage_returns_parsed_pydantic_model(self) -> None:
        tasks = [_task(id="1", content="Write report"), _task(id="2", content="Email team")]
        raw_response = TriagePlanIDs(
            big=["1"],
            medium=["2"],
            small=[],
            quadrant="urgent_important",
            domain="work",
        )

        fake_instructor = self._fake_instructor()
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = raw_response
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
        fake_client.chat.completions.create.return_value = TriagePlanIDs()

        with self._patch_instructor(fake_instructor):
            self._service().plan_triage(tasks=tasks, free_blocks=[])

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
        fake_client.chat.completions.create.return_value = TriagePlanIDs()

        with self._patch_instructor(fake_instructor):
            self._service().plan_triage(tasks=tasks, free_blocks=blocks)

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        prompt_text = " ".join(m.get("content", "") for m in messages)
        assert "09:00" in prompt_text

    def test_plan_triage_uses_response_format_model(self) -> None:
        """Verify the LLM is asked to return structured JSON matching TriagePlanIDs."""
        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        fake_client.chat.completions.create.return_value = TriagePlanIDs()

        with self._patch_instructor(fake_instructor):
            self._service().plan_triage(tasks=[], free_blocks=[])

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        # instructor passes the pydantic model as response_model
        assert call_kwargs.get("response_model") is TriagePlanIDs
        assert call_kwargs.get("max_tokens") == 1024

    def test_plan_triage_handles_empty_task_list(self) -> None:
        fake_instructor = self._fake_instructor()
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = TriagePlanIDs()
        fake_instructor.from_openai.return_value = fake_client

        with self._patch_instructor(fake_instructor):
            result = self._service().plan_triage(tasks=[], free_blocks=[])

        assert isinstance(result, TriagePlan)
        assert result.big == []
        assert result.medium == []
        assert result.small == []

    def test_plan_triage_sends_system_message_for_json_mode(self) -> None:
        """Verify the system prompt instructs the LLM to return only JSON."""
        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        fake_client.chat.completions.create.return_value = TriagePlanIDs()

        with self._patch_instructor(fake_instructor):
            self._service().plan_triage(tasks=[], free_blocks=[])

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert "JSON" in system_msg["content"]

    def test_plan_triage_propagates_instructor_errors(self) -> None:
        """If instructor raises, the exception should propagate to the caller."""
        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        fake_client.chat.completions.create.side_effect = ValueError("invalid JSON")

        with self._patch_instructor(fake_instructor):
            with pytest.raises(ValueError, match="invalid JSON"):
                self._service().plan_triage(tasks=[], free_blocks=[])

    def test_plan_triage_hydrates_task_ids_and_postpones_unassigned_tasks(self) -> None:
        """Verify tasks not assigned by LLM are auto-appended to postponed list."""
        t1 = _task(id="t1", content="Task 1")
        t2 = _task(id="t2", content="Task 2")
        t3 = _task(id="t3", content="Task 3")
        tasks = [t1, t2, t3]

        raw_response = TriagePlanIDs(big=["t1"], medium=[], small=[], postponed=[])

        fake_instructor = self._fake_instructor()
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = raw_response
        fake_instructor.from_openai.return_value = fake_client

        with self._patch_instructor(fake_instructor):
            result = self._service().plan_triage(tasks=tasks, free_blocks=[])

        assert [t.id for t in result.big] == ["t1"]
        assert [t.id for t in result.postponed] == ["t2", "t3"]

    def test_constructor_configures_instructor_json_mode(self) -> None:
        """LLMService should use instructor.Mode.MD_JSON for llama.cpp compatibility."""
        with patch("services.llm_service.instructor") as mock_instructor:
            mock_instructor.from_openai.return_value = MagicMock()
            mock_instructor.Mode.MD_JSON = "md-json-mode"

            LLMService(model="test", base_url="http://localhost:8000/v1")

            call_kwargs = mock_instructor.from_openai.call_args
            assert call_kwargs.kwargs.get("mode") == "md-json-mode"

    def test_constructor_uses_correct_model_name(self) -> None:
        """The model name passed to LLMService must reach the inner client."""
        service = LLMService(model="gemma-4-12b-it-Q4_K_M.gguf", base_url="http://x")
        assert service._model == "gemma-4-12b-it-Q4_K_M.gguf"

    def test_constructor_passes_base_url_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenAI client must be constructed with the provided base_url and api_key."""
        captured: dict = {}
        from openai import OpenAI as RealOpenAI

        def spy_init(self: RealOpenAI, **kwargs: object) -> None:
            captured["init_kwargs"] = kwargs
            # Skip network calls during __init__
            self.base_url = kwargs.get("base_url", "")
            self.api_key = kwargs.get("api_key", "")
            self.timeout = kwargs.get("timeout", None)

        monkeypatch.setattr(RealOpenAI, "__init__", spy_init)
        LLMService(
            model="m",
            base_url="http://my-ollama:11434/v1",
            api_key="custom-key",
            timeout=90.0,
        )

        assert captured["init_kwargs"]["base_url"] == "http://my-ollama:11434/v1"
        assert captured["init_kwargs"]["api_key"] == "custom-key"
        assert captured["init_kwargs"]["timeout"].read == 90.0

    def test_from_env_reads_gemini_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLMService.from_env() prioritizes LLM_BASE_URL, LLM_MODEL, and LLM_API_KEY."""
        monkeypatch.setenv(
            "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        monkeypatch.setenv("LLM_MODEL", "gemini-2.0-flash")
        monkeypatch.setenv("LLM_API_KEY", "AIzaSy-test-key")

        service = LLMService.from_env()
        assert service._model == "gemini-2.0-flash"

    def test_from_env_falls_back_to_ollama_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLMService.from_env() falls back to OLLAMA_* when LLM_* are unset."""
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:8000/v1")
        monkeypatch.setenv("OLLAMA_MODEL", "gemma4")

        service = LLMService.from_env()
        assert service._model == "gemma4"


class TestBuildPrompt:
    """Direct tests for the `_build_prompt` static helper."""

    def test_format_includes_all_task_fields(self) -> None:
        tasks = [Task(id="42", content="Ship release", project_id="p1", due_date=date(2026, 6, 30))]
        prompt = LLMService._build_prompt(tasks=tasks, free_blocks=[])
        assert "Ship release" in prompt
        assert "42" in prompt
        assert "2026-06-30" in prompt

    def test_format_uses_placeholder_when_no_tasks(self) -> None:
        prompt = LLMService._build_prompt(tasks=[], free_blocks=[])
        assert "no tasks due today" in prompt

    def test_format_uses_placeholder_when_no_free_blocks(self) -> None:
        from models.task import TimeBlock

        blocks = [TimeBlock(start="2026-06-28T09:00:00+00:00", end="2026-06-28T10:00:00+00:00")]
        prompt = LLMService._build_prompt(tasks=[], free_blocks=blocks)
        assert "09:00" in prompt
        assert "no tasks due today" in prompt

    def test_format_lists_multiple_free_blocks(self) -> None:
        from models.task import TimeBlock

        blocks = [
            TimeBlock(start="2026-06-28T08:00:00+00:00", end="2026-06-28T09:00:00+00:00"),
            TimeBlock(start="2026-06-28T14:00:00+00:00", end="2026-06-28T15:00:00+00:00"),
        ]
        prompt = LLMService._build_prompt(tasks=[], free_blocks=blocks)
        assert "08:00" in prompt
        assert "14:00" in prompt
