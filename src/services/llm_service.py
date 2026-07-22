"""LLM triage service.

Sends today's tasks and free-time blocks to a local LLM (llama-server /
Ollama) and gets back a structured 1-3-5 daily plan. Uses `instructor` to
enforce that the response parses into a Pydantic `TriagePlan` model —
never free-text.

Mode is `instructor.Mode.JSON` so the integration works with servers that
don't implement the OpenAI `tool_calls` protocol (llama.cpp / llama-server);
switch to `Mode.TOOLS` if your server supports function calling natively.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import instructor
from openai import OpenAI

from models.task import Task, TriagePlan


class LLMService:
    """Talks to any OpenAI-compatible LLM and returns a structured triage plan."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "ollama",
        timeout: float = 600.0,
        cache_dir: str = "",
    ) -> None:
        raw_client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(timeout, connect=10.0, read=timeout),
        )
        self._client = instructor.from_openai(raw_client, mode=instructor.Mode.MD_JSON)
        self._model = model
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> LLMService:
        """Instantiate LLMService from environment variables (.env).

        Supports local LLMs (llama-server / Ollama) as well as OpenRouter,
        Google Gemini OpenAI endpoint, or OpenAI directly without code duplication.
        """
        model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL", "gemma4")
        base_url = os.getenv("LLM_BASE_URL") or os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:8000/v1"
        )
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OLLAMA_API_KEY", "ollama")
        timeout = float(os.getenv("LLM_TIMEOUT", "600.0"))
        cache_dir = os.getenv("LLM_CACHE_DIR", ".cache")
        return cls(
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            cache_dir=cache_dir,
        )

    @classmethod
    def _compute_input_hash(
        cls, tasks: list[Task], free_blocks: list[Any], reference_date: date | None = None
    ) -> str:
        """Compute a deterministic SHA-256 hash of the input payload."""
        ref = (reference_date or date.today()).isoformat()
        task_data = sorted([f"{t.id}:{t.content}:{t.due_date}" for t in tasks])
        block_data = sorted([f"{b.start}:{b.end}" for b in free_blocks])
        payload = json.dumps({"ref": ref, "tasks": task_data, "blocks": block_data}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def plan_triage(
        self,
        tasks: list[Task],
        free_blocks: list[Any],
        *,
        force_refresh: bool = False,
        reference_date: date | None = None,
    ) -> TriagePlan:
        """Ask the LLM to propose a 1-3-5 plan for today (uses disk cache if payload matches)."""
        payload_hash = self._compute_input_hash(tasks, free_blocks, reference_date)
        cache_file = self._cache_dir / f"triage_{payload_hash}.json" if self._cache_dir else None

        if not force_refresh and cache_file and cache_file.exists():
            try:
                cached_data = cache_file.read_text(encoding="utf-8")
                return TriagePlan.model_validate_json(cached_data)
            except Exception:
                pass  # Fallback to fresh LLM call if cache reading fails

        prompt = self._build_prompt(tasks, free_blocks)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Olet päivän triage-assistentti. Säännöt:\n"
                        "1. P1/P2 ja 1-3pv myöhästyneet tehtävät ovat 'big' ja 'medium'.\n"
                        "2. Yli 7pv myöhästyneet 'STALE'-tehtävät menevät 'postponed'-listalle.\n"
                        "3. Kesto <= 15m tehtävät ovat 'small'.\n"
                        "Valitse 1 Big, 3 Medium, 5 Small -tehtävää.\n"
                        "Palauta JSON ('big', 'medium', 'small', 'postponed')."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_model=TriagePlan,
            max_tokens=4096,
        )

        if cache_file:
            try:
                cache_file.write_text(response.model_dump_json(), encoding="utf-8")
            except Exception:
                pass

        return response

    @staticmethod
    def _format_task(t: Task) -> str:
        prio_str = f"P{5 - t.priority}" if 1 <= t.priority <= 4 else f"P{t.priority}"
        if t.is_stale:
            overdue_str = f" [STALE: {t.days_overdue}d overdue]"
        elif t.is_overdue:
            overdue_str = f" [RECENTLY OVERDUE: {t.days_overdue}d]"
        else:
            overdue_str = ""
        dur_str = f", dur: {t.duration_minutes}m" if t.duration_minutes else ""
        info = f"(due: {t.due_date}{overdue_str}, priority: {prio_str}{dur_str})"
        return f"- [{t.id}] {t.content} {info}"

    @classmethod
    def _build_prompt(cls, tasks: list[Task], free_blocks: list[Any]) -> str:
        task_lines = "\n".join(cls._format_task(t) for t in tasks) or "(no tasks due today)"

        block_lines = (
            "\n".join(f"- {b.start} to {b.end}" for b in free_blocks)
            or "(no free blocks identified)"
        )

        return (
            f"Today's tasks:\n{task_lines}\n\n"
            f"Free time blocks:\n{block_lines}\n\n"
            "Propose a 1-3-5 plan for today and list remaining tasks under postponed."
        )
