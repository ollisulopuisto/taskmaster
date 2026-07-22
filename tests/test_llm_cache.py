"""Tests for LLM API caching and payload hash minimization."""

from datetime import date
from unittest.mock import MagicMock

from models.task import Task, TimeBlock, TriagePlan
from services.llm_service import LLMService


def test_compute_payload_hash_is_deterministic():
    t1 = Task(id="1", content="Task A", project_id="p1", due_date=date(2026, 7, 22))
    t2 = Task(id="2", content="Task B", project_id="p1", due_date=date(2026, 7, 22))
    b1 = TimeBlock(start="2026-07-22T09:00:00", end="2026-07-22T10:00:00")

    hash1 = LLMService._compute_input_hash([t1, t2], [b1], reference_date=date(2026, 7, 22))
    hash2 = LLMService._compute_input_hash([t1, t2], [b1], reference_date=date(2026, 7, 22))

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 string


def test_plan_triage_uses_cache_on_identical_input(tmp_path):
    t1 = Task(id="1", content="Task A", project_id="p1", due_date=date(2026, 7, 22))
    b1 = TimeBlock(start="2026-07-22T09:00:00", end="2026-07-22T10:00:00")

    mock_plan = TriagePlan(big=[t1])

    service = LLMService(model="test", base_url="http://fake", cache_dir=str(tmp_path))
    service._client = MagicMock()
    service._client.chat.completions.create.return_value = mock_plan

    # First call: hits LLM, populates cache
    plan1 = service.plan_triage([t1], [b1], reference_date=date(2026, 7, 22))
    assert service._client.chat.completions.create.call_count == 1
    assert plan1.big[0].id == "1"

    # Second call with identical input: hits disk cache, 0 LLM calls!
    plan2 = service.plan_triage([t1], [b1], reference_date=date(2026, 7, 22))
    assert service._client.chat.completions.create.call_count == 1  # count did NOT increase
    assert plan2.big[0].id == "1"


def test_plan_triage_force_refresh_bypasses_cache(tmp_path):
    t1 = Task(id="1", content="Task A", project_id="p1", due_date=date(2026, 7, 22))
    mock_plan = TriagePlan(big=[t1])

    service = LLMService(model="test", base_url="http://fake", cache_dir=str(tmp_path))
    service._client = MagicMock()
    service._client.chat.completions.create.return_value = mock_plan

    service.plan_triage([t1], [], reference_date=date(2026, 7, 22))
    assert service._client.chat.completions.create.call_count == 1

    # Force refresh bypasses cache
    service.plan_triage([t1], [], force_refresh=True, reference_date=date(2026, 7, 22))
    assert service._client.chat.completions.create.call_count == 2
