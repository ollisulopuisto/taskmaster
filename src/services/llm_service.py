"""LLM triage service.

Sends today's tasks and free-time blocks to a local LLM (Ollama) and gets
back a structured 1-3-5 daily plan. Uses `instructor` to enforce that the
response parses into a Pydantic `TriagePlan` model — never free-text.
"""

from __future__ import annotations

from typing import Any

import instructor
from openai import OpenAI

from models.task import Task, TriagePlan


class LLMService:
    """Talks to a local LLM and returns a structured triage plan."""

    def __init__(self, model: str, base_url: str, api_key: str = "ollama") -> None:
        raw_client = OpenAI(base_url=base_url, api_key=api_key)
        self._client = instructor.from_openai(raw_client)
        self._model = model

    def plan_triage(self, tasks: list[Task], free_blocks: list[Any]) -> TriagePlan:
        """Ask the LLM to propose a 1-3-5 plan for today."""
        prompt = self._build_prompt(tasks, free_blocks)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a daily triage assistant. Given a user's tasks and "
                        "free time blocks, propose a 1-3-5 plan: 1 big task, 3 medium "
                        "tasks, and 5 small tasks. Assign each an Eisenhower quadrant "
                        "(urgent_important, not_urgent_important, urgent_not_important, "
                        "neither) and a domain (work, civilian, family). Return ONLY "
                        "valid JSON matching the provided schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_model=TriagePlan,
        )
        return response

    @staticmethod
    def _build_prompt(tasks: list[Task], free_blocks: list[Any]) -> str:
        task_lines = (
            "\n".join(
                f"- [{t.id}] {t.content} (due: {t.due_date}, priority: {t.priority})" for t in tasks
            )
            or "(no tasks due today)"
        )

        block_lines = (
            "\n".join(f"- {b.start} to {b.end}" for b in free_blocks)
            or "(no free blocks identified)"
        )

        return (
            f"Today's tasks:\n{task_lines}\n\n"
            f"Free time blocks:\n{block_lines}\n\n"
            "Propose a 1-3-5 plan for today."
        )
