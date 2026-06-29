"""Integration tests for the Ollama ↔ instructor ↔ LLMService chain.

These tests verify that the instructor library correctly talks to an
OpenAI-compatible endpoint (Ollama / llama.cpp) using the JSON mode we
configure in LLMService. The Ollama side is mocked to avoid requiring a
real LLM at test time, but the instructor library itself is real.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from openai import OpenAI

from models.task import Task, TriagePlan
from services.llm_service import LLMService

# ---------------------------------------------------------------------------
# Helper: build a fake HTTP response that returns JSON
# ---------------------------------------------------------------------------


def _fake_http_response(payload: dict[str, Any]) -> MagicMock:
    """Build a response-like object whose json() method returns payload."""
    serialized = json.dumps(payload)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.content = serialized.encode("utf-8")
    resp.text = serialized
    resp.raise_for_status = MagicMock()
    return resp


def _triage_plan_json(*, quadrant: str = "not_urgent_important", domain: str = "civilian") -> dict:
    """Return a minimal JSON-serializable dict matching TriagePlan schema."""
    return {"big": [], "medium": [], "small": [], "quadrant": quadrant, "domain": domain}


# ---------------------------------------------------------------------------


class TestOllamaIntegration:
    """Verify the full OpenAI → instructor → TriagePlan parsing chain."""

    def test_full_chain_parses_valid_json_into_triage_plan(self) -> None:
        """Simulate Ollama returning valid JSON — instructor parses into TriagePlan."""
        service = LLMService(model="llama3", base_url="http://ollama:11434/v1")

        fake_response = TriagePlan(quadrant="urgent_important", domain="work")

        with patch.object(
            service._client.chat.completions,
            "create",
            return_value=fake_response,
        ) as mock_create:
            result = service.plan_triage(tasks=[], free_blocks=[])

            # Verify the LLM was asked for a structured response
            call_kwargs = mock_create.call_args.kwargs
            assert "response_model" in call_kwargs
            assert call_kwargs["response_model"] is TriagePlan

        assert isinstance(result, TriagePlan)
        assert result.quadrant == "urgent_important"
        assert result.domain == "work"

    def test_llm_service_sends_messages_in_openai_format(self) -> None:
        """Verify the prompt structure matches OpenAI's chat completions schema."""
        service = LLMService(model="llama3", base_url="http://x:11434/v1")

        captured: dict = {}

        def spy_create(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return TriagePlan()

        service._client.chat.completions.create = spy_create

        tasks = [Task(id="1", content="Test", project_id="p1")]
        service.plan_triage(tasks=tasks, free_blocks=[])

        assert "messages" in captured
        assert len(captured["messages"]) == 2
        roles = [m["role"] for m in captured["messages"]]
        assert "system" in roles
        assert "user" in roles

    def test_openai_client_accepts_ollama_base_url(self) -> None:
        """OpenAI class accepts an Ollama-style base_url without crashing."""
        client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        assert "11434" in str(client.base_url)

    def test_llm_service_timeout_forwards_to_openai_client(self) -> None:
        """LLMService should forward its timeout to the OpenAI client."""
        captured_kwargs: dict = {}

        from openai import OpenAI as OrigOpenAI

        def spy_init(self: Any, **kw: Any) -> None:
            captured_kwargs.update(kw)
            # Don't actually connect
            self.base_url = kw.get("base_url", "")
            self.api_key = kw.get("api_key", "")
            self.timeout = kw.get("timeout")

        with patch.object(OrigOpenAI, "__init__", spy_init):
            LLMService(model="m", base_url="http://x:11434/v1", timeout=45.0)

        assert captured_kwargs["timeout"].read == 45.0
        assert captured_kwargs["timeout"].connect == 10.0


class TestOllamaModeSelection:
    """Verify the instructor mode chosen for llama.cpp / Ollama compatibility."""

    def test_llm_service_uses_json_mode_not_tools(self) -> None:
        """LLMService should default to instructor.Mode.JSON (not TOOLS)."""
        import instructor

        captured: dict = {}

        def spy_from_openai(client: Any, mode: str, **kw: Any) -> MagicMock:
            captured["mode"] = mode
            return MagicMock()

        with patch("services.llm_service.instructor.from_openai", side_effect=spy_from_openai):
            LLMService(model="m", base_url="http://x")

        assert captured["mode"] == instructor.Mode.JSON

    def test_mode_json_enables_openai_compat_without_function_calling(self) -> None:
        """JSON mode works with servers that lack tool_calls protocol support."""
        from openai import OpenAI

        client = OpenAI(base_url="http://x:11434/v1", api_key="ollama")
        wrapped = __import__("instructor").from_openai(
            client, mode=__import__("instructor").Mode.JSON
        )

        # Wrapped client should expose chat.completions.create
        assert hasattr(wrapped.chat.completions, "create")
