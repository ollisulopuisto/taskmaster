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

from models.task import LLMBackendConfig, Task, TriagePlan, TriagePlanIDs


class LLMService:
    """Talks to any OpenAI-compatible LLM and returns a structured triage plan."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "ollama",
        timeout: float = 600.0,
        cache_dir: str = "",
        max_tokens: int = 8192,
    ) -> None:
        raw_client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(timeout, connect=10.0, read=timeout),
        )
        self._client = instructor.from_openai(raw_client, mode=instructor.Mode.MD_JSON)
        self._model = model
        self._max_tokens = max_tokens
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate_backend(cls, config: LLMBackendConfig) -> tuple[bool, str]:
        """Fast pre-check to verify LLM backend reachability and API key validity."""
        if not config.base_url or not config.base_url.startswith("http"):
            return (
                False,
                f"LLM backend '{config.name}' has an invalid base URL: {config.base_url!r}",
            )

        headers = {}
        if config.api_key and config.api_key.lower() not in ("ollama", "none", ""):
            headers["Authorization"] = f"Bearer {config.api_key}"

        client = httpx.Client(timeout=3.0)
        try:
            models_url = f"{config.base_url.rstrip('/')}/models"
            res = client.get(models_url, headers=headers)
            if res.status_code == 200:
                return True, f"LLM backend '{config.name}' connected (HTTP 200)."
            if res.status_code in (401, 403):
                return (
                    False,
                    f"LLM backend '{config.name}' API key rejected (HTTP {res.status_code}). "
                    "Check API key in .env.",
                )

            root_url = config.base_url.rsplit("/v1", 1)[0]
            tags_res = client.get(f"{root_url}/api/tags")
            if tags_res.status_code == 200:
                return True, f"LLM backend '{config.name}' connected via Ollama API."

            return (
                False,
                f"LLM backend '{config.name}' returned HTTP {res.status_code}.",
            )
        except Exception as exc:
            return (
                False,
                f"LLM backend '{config.name}' unreachable at {config.base_url}: {exc}",
            )
        finally:
            client.close()

    @classmethod
    def discover_local_backends(
        cls, target_ports: list[int] | None = None
    ) -> dict[str, LLMBackendConfig]:
        """Probe local ports for running OpenAI-compatible or Ollama LLM servers."""
        ports = target_ports if target_ports is not None else [8000, 11434, 1234]
        discovered: dict[str, LLMBackendConfig] = {}

        client = httpx.Client(timeout=2.0)
        try:
            for port in ports:
                base_v1 = f"http://localhost:{port}/v1"
                # 1. Try standard OpenAI /v1/models
                try:
                    res = client.get(f"{base_v1}/models")
                    if res.status_code == 200:
                        data = res.json()
                        models_list = data.get("data", [])
                        for item in models_list:
                            model_id = item.get("id") if isinstance(item, dict) else str(item)
                            if model_id:
                                clean_id = model_id.replace(":", "_").replace("/", "_")
                                key = f"auto_{port}_{clean_id}"
                                name = f"⚡ Discovered (Port {port}): {model_id}"
                                discovered[key] = LLMBackendConfig(
                                    key=key,
                                    name=name,
                                    base_url=base_v1,
                                    model=model_id,
                                    api_key="ollama",
                                )
                except Exception:
                    pass

                # 2. Try Ollama native /api/tags
                if port == 11434 or not any(k.startswith(f"auto_{port}_") for k in discovered):
                    try:
                        res = client.get(f"http://localhost:{port}/api/tags")
                        if res.status_code == 200:
                            data = res.json()
                            models_list = data.get("models", [])
                            for item in models_list:
                                model_name = (
                                    item.get("name") if isinstance(item, dict) else str(item)
                                )
                                if model_name:
                                    clean_name = model_name.replace(":", "_").replace("/", "_")
                                    key = f"auto_{port}_{clean_name}"
                                    if key not in discovered:
                                        name = f"⚡ Discovered Ollama (Port {port}): {model_name}"
                                        discovered[key] = LLMBackendConfig(
                                            key=key,
                                            name=name,
                                            base_url=base_v1,
                                            model=model_name,
                                            api_key="ollama",
                                        )
                    except Exception:
                        pass
        finally:
            client.close()

        return discovered

    @classmethod
    def get_available_backends(cls, autodiscover: bool = False) -> dict[str, LLMBackendConfig]:
        """Discover available LLM backends defined in environment variables.

        Supports multi-backend configuration via LLM_BACKENDS (comma-separated list of keys,
        e.g. `local, gemini`) with per-backend settings `LLM_BACKEND_<KEY>_*`.

        If `autodiscover` is True, also probes running local servers (Ollama, llama-server, etc.).

        Falls back to reading single LLM_* or OLLAMA_* variables if LLM_BACKENDS is not defined.
        """
        raw_backends = os.getenv("LLM_BACKENDS", "").strip()
        backends: dict[str, LLMBackendConfig] = {}

        if raw_backends:
            keys = [k.strip() for k in raw_backends.split(",") if k.strip()]
            for k in keys:
                upper_key = k.upper()
                name = os.getenv(f"LLM_BACKEND_{upper_key}_NAME", f"Backend {k}")
                base_url = os.getenv(
                    f"LLM_BACKEND_{upper_key}_BASE_URL", "http://localhost:8000/v1"
                )
                model = os.getenv(f"LLM_BACKEND_{upper_key}_MODEL", "gemma4")
                api_key = os.getenv(f"LLM_BACKEND_{upper_key}_API_KEY", "ollama")
                timeout = float(os.getenv(f"LLM_BACKEND_{upper_key}_TIMEOUT", "600.0"))
                backends[k] = LLMBackendConfig(
                    key=k,
                    name=name,
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    timeout=timeout,
                )

        if not backends:
            model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL", "gemma4")
            base_url = os.getenv("LLM_BASE_URL") or os.getenv(
                "OLLAMA_BASE_URL", "http://localhost:8000/v1"
            )
            api_key = os.getenv("LLM_API_KEY") or os.getenv("OLLAMA_API_KEY", "ollama")
            timeout = float(os.getenv("LLM_TIMEOUT", "600.0"))
            name = os.getenv("LLM_NAME", f"Default LLM ({model})")
            backends["default"] = LLMBackendConfig(
                key="default",
                name=name,
                base_url=base_url,
                model=model,
                api_key=api_key,
                timeout=timeout,
            )

        if autodiscover:
            discovered = cls.discover_local_backends()
            backends.update(discovered)

        return backends

    @classmethod
    def from_env(cls, backend_key: str | None = None) -> LLMService:
        """Instantiate LLMService from environment variables (.env).

        If `backend_key` is provided, selects that configured backend.
        Otherwise uses `LLM_DEFAULT_BACKEND` or the first available backend.
        """
        backends = cls.get_available_backends()
        target_key = backend_key or os.getenv("LLM_DEFAULT_BACKEND", "").strip()

        if target_key and target_key not in backends:
            backends = cls.get_available_backends(autodiscover=True)

        if target_key and target_key in backends:
            config = backends[target_key]
        else:
            config = next(iter(backends.values()))

        cache_dir = os.getenv("LLM_CACHE_DIR", ".cache")
        return cls(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
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
        raw_plan: TriagePlanIDs = self._client.chat.completions.create(
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
                        "TÄRKEÄÄ: Ole tiivis. Älä generoi pitkää päättelyketjua "
                        "(no long CoT reasoning).\n"
                        "Palauta ainoastaan JSON tehtävien ID-luettelona "
                        "('big', 'medium', 'small', 'postponed')."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_model=TriagePlanIDs,
            max_tokens=self._max_tokens,
        )

        def _get_id(item: Any) -> str:
            return item.id if hasattr(item, "id") else str(item)

        task_map = {t.id: t for t in tasks}
        big_ids = [_get_id(x) for x in raw_plan.big]
        medium_ids = [_get_id(x) for x in raw_plan.medium]
        small_ids = [_get_id(x) for x in raw_plan.small]
        postponed_ids = [_get_id(x) for x in raw_plan.postponed]

        big_tasks = [task_map[tid] for tid in big_ids if tid in task_map]
        medium_tasks = [task_map[tid] for tid in medium_ids if tid in task_map]
        small_tasks = [task_map[tid] for tid in small_ids if tid in task_map]
        postponed_tasks = [task_map[tid] for tid in postponed_ids if tid in task_map]

        assigned_ids = set(big_ids + medium_ids + small_ids + postponed_ids)
        for t in tasks:
            if t.id not in assigned_ids:
                postponed_tasks.append(t)

        hydrated_plan = TriagePlan(
            big=big_tasks,
            medium=medium_tasks,
            small=small_tasks,
            postponed=postponed_tasks,
            quadrant=raw_plan.quadrant,
            domain=raw_plan.domain,
        )

        if cache_file:
            try:
                cache_file.write_text(hydrated_plan.model_dump_json(), encoding="utf-8")
            except Exception:
                pass

        return hydrated_plan

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
