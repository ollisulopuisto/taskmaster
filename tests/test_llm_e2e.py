"""End-to-end test that hits the real llama-server on port 8000.

Run manually when llama-server is up:
    uv run pytest tests/test_llm_e2e.py -v

Skipped automatically in CI (no llama-server).
"""

from __future__ import annotations

import httpx
import pytest

from models.task import Task, TriagePlan
from services.llm_service import LLMService


# Skip if llama-server not reachable
def _llama_server_up() -> bool:
    try:
        r = httpx.get("http://localhost:8000/v1/models", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


skip_if_no_llama = pytest.mark.skipif(
    not _llama_server_up(),
    reason="llama-server not running on http://localhost:8000",
)


@skip_if_no_llama
class TestLLME2E:
    """Real requests against local llama-server."""

    def test_health_check_models_endpoint(self) -> None:
        """Basic connectivity: /v1/models should return 200."""
        r = httpx.get("http://localhost:8000/v1/models", timeout=5.0)
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        # Verify our model is loaded
        model_ids = [m["id"] for m in data["data"]]
        assert any("gemma" in m.lower() for m in model_ids), (
            f"Expected gemma model, got: {model_ids}"
        )

    @pytest.mark.slow
    def test_chat_completions_returns_json(self) -> None:
        """Raw OpenAI-compatible chat/completions returns valid JSON."""
        payload = {
            "model": "gemma-4-12b-it-Q4_K_M.gguf",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        'Return only JSON: {"big":[],"medium":[],"small":[],"quadrant":"'
                        'not_urgent_important","domain":"civilian"}'
                    ),
                },
                {"role": "user", "content": "No tasks today."},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        }
        r = httpx.post("http://localhost:8000/v1/chat/completions", json=payload, timeout=30.0)
        assert r.status_code == 200
        data = r.json()
        assert "choices" in data
        content = data["choices"][0]["message"]["content"]
        # Should be parseable JSON
        import json

        parsed = json.loads(content)
        assert set(parsed.keys()) >= {"big", "medium", "small", "quadrant", "domain"}

    @pytest.mark.slow
    def test_llm_service_plan_triage_real(self) -> None:
        """Full LLMService.plan_triage() against real llama-server (slow)."""
        service = LLMService(
            model="gemma-4-12b-it-Q4_K_M.gguf",
            base_url="http://localhost:8000/v1",
            timeout=180.0,
        )

        tasks = [
            Task(id="1", content="Write quarterly report", project_id="work"),
            Task(id="2", content="Buy groceries", project_id="personal"),
            Task(id="3", content="Call mom", project_id="family"),
        ]

        result = service.plan_triage(tasks=tasks, free_blocks=[])

        assert isinstance(result, TriagePlan)
        # At minimum, structure should be valid
        assert isinstance(result.big, list)
        assert isinstance(result.medium, list)
        assert isinstance(result.small, list)
        assert result.quadrant in {
            "urgent_important",
            "not_urgent_important",
            "urgent_not_important",
            "neither",
        }
        assert result.domain in {"work", "civilian", "family"}

    @pytest.mark.slow
    def test_llm_service_handles_empty_tasks_real(self) -> None:
        """Empty task list should still returns valid plan (slow)."""
        service = LLMService(
            model="gemma-4-12b-it-Q4_K_M.gguf",
            base_url="http://localhost:8000/v1",
            timeout=120.0,
        )
        result = service.plan_triage(tasks=[], free_blocks=[])
        assert isinstance(result, TriagePlan)
        assert result.big == []
        assert result.medium == []
        assert result.small == []
