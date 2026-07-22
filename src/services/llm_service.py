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

from typing import Any

import httpx
import instructor
from openai import OpenAI

from models.task import Task, TriagePlan


class LLMService:
    """Talks to a local LLM and returns a structured triage plan."""

    def __init__(
        self, model: str, base_url: str, api_key: str = "ollama", timeout: float = 600.0
    ) -> None:
        raw_client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(timeout, connect=10.0, read=timeout),
        )
        self._client = instructor.from_openai(raw_client, mode=instructor.Mode.MD_JSON)
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
                        "Olet päivän triage-assistentti. Käyttäjä antaa tehtäviä "
                        "ja vapaa-aikoja. Valitse annetuista tehtävistä 1-3-5 -suunnitelma: "
                        "1 Big, 3 Medium ja 5 Small -tehtävää. "
                        "Palauta validi JSON, jossa 'big', 'medium' ja 'small' ovat "
                        "tehtäväolioiden listoja "
                        "(id, content, project_id, due_date, labels, priority)."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_model=TriagePlan,
            max_tokens=4096,
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
