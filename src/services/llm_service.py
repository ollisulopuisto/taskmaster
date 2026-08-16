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
import logging
import os
import re
import socket
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import instructor
from openai import OpenAI

from models.task import (
    DailySchedule,
    LLMBackendConfig,
    Task,
    TriageMode,
    TriagePlan,
    TriagePlanIDs,
)

try:
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
except ImportError:
    ServiceBrowser = None  # type: ignore[assignment]
    ServiceStateChange = None  # type: ignore[assignment]
    Zeroconf = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

# mDNS service types that LLM servers advertise on the LAN.
LAN_LLM_SERVICE_TYPES = ["_ollama._tcp.local.", "_llm._tcp.local.", "_openai._tcp.local."]
# Well-known ports probed during the opt-in subnet scan.
LAN_LLM_PORTS = [11434, 8000, 1234]
# Results from mDNS browsing are cached to avoid re-probing on every UI action.
LAN_CACHE_TTL = 120.0


class LLMService:
    """Talks to any OpenAI-compatible LLM and returns a structured triage plan."""

    # TTL-guarded cache of mDNS LAN discovery results (see LAN_CACHE_TTL).
    _lan_cache: dict[str, LLMBackendConfig] = {}
    _lan_cache_expires: float = 0.0

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
    def discover_lan_backends(
        cls,
        browse_seconds: float = 2.0,
        probe_timeout: float = 2.0,
        use_cache: bool = True,
    ) -> dict[str, LLMBackendConfig]:
        """Discover LLM servers on the LAN via mDNS/ZeroConf (Bonjour/avahi).

        Browses the well-known LLM service types (`_ollama._tcp.local.`,
        `_llm._tcp.local.`, `_openai._tcp.local.`) — servers advertise
        themselves, no IP scanning needed — then resolves
        each advertised host:port and probes it for an OpenAI-compatible
        `/v1/models` or Ollama `/api/tags` endpoint.

        This is passive and fast, so it runs on every autodiscovery. Results are
        cached for `LAN_CACHE_TTL` seconds unless ``use_cache`` is False.
        """
        if ServiceBrowser is None or Zeroconf is None:
            return {}

        now = time.monotonic()
        if use_cache and cls._lan_cache and now < cls._lan_cache_expires:
            return dict(cls._lan_cache)

        found_names: list[tuple[str, str]] = []

        def _on_change(_zc: Any, service_type: str, name: str, state_change: Any) -> None:
            if state_change == ServiceStateChange.Added:
                found_names.append((service_type, name))

        discovered: dict[str, LLMBackendConfig] = {}
        try:
            zc = Zeroconf()
            try:
                ServiceBrowser(zc, LAN_LLM_SERVICE_TYPES, [_on_change])
                time.sleep(browse_seconds)
                for service_type, name in found_names:
                    info = zc.get_service_info(service_type, name)
                    if not info or not info.port:
                        continue
                    addresses = list(info.parsed_addresses() or [])
                    if not addresses:
                        continue
                    host = addresses[0]
                    probed = cls._probe_host_models(host, info.port, timeout=probe_timeout)
                    if not probed:
                        continue
                    label, model_ids = probed
                    for model_id in model_ids:
                        cfg = cls._make_lan_config(host, info.port, model_id, label)
                        if cfg.key not in discovered:
                            discovered[cfg.key] = cfg
            finally:
                zc.close()
        except Exception:
            pass  # Discovery is best-effort; never crash the app over it.

        cls._lan_cache = discovered
        cls._lan_cache_expires = now + LAN_CACHE_TTL
        return dict(discovered)

    @staticmethod
    def _try_connect(host: str, port: int, timeout: float) -> bool:
        """True if `host:port` accepts a TCP connection within `timeout`."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _guess_local_ip() -> str:
        """Determine the machine's primary LAN IPv4 without sending packets."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"

    @classmethod
    def _guess_lan_hosts(cls) -> list[str]:
        """All /24 neighbours of this machine (hosts .1-.254)."""
        local_ip = cls._guess_local_ip()
        if local_ip.startswith("127."):
            return ["127.0.0.1"]
        prefix = ".".join(local_ip.split(".")[:3])
        return [f"{prefix}.{i}" for i in range(1, 255)]

    @classmethod
    def scan_lan_ports(
        cls,
        ports: list[int] | None = None,
        hosts: list[str] | None = None,
        timeout: float = 0.5,
        max_workers: int = 64,
    ) -> dict[str, LLMBackendConfig]:
        """Probe hosts on the LAN for LLM servers on well-known ports.

        Uses parallel short-timeout TCP connects, then HTTP-verifies anything
        that answers. Slower and noisier than mDNS — the caller decides when to
        run it (default: opt-in via ``LLM_LAN_SCAN=1`` or explicit hosts).
        """
        discovered: dict[str, LLMBackendConfig] = {}
        targets = hosts or cls._guess_lan_hosts()
        ports = ports or LAN_LLM_PORTS

        pairs = [(host, port) for host in targets for port in ports]
        if not pairs:
            return discovered

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(cls._try_connect, host, port, timeout): (host, port)
                for host, port in pairs
            }
            for future in as_completed(futures):
                host, port = futures[future]
                if not future.result():
                    continue
                probed = cls._probe_host_models(host, port, timeout=max(1.0, timeout * 4))
                if not probed:
                    continue
                label, model_ids = probed
                for model_id in model_ids:
                    cfg = cls._make_lan_config(host, port, model_id, label)
                    if cfg.key not in discovered:
                        discovered[cfg.key] = cfg

        return discovered

    @classmethod
    def _probe_host_models(
        cls, host: str, port: int, timeout: float = 2.0
    ) -> tuple[str, list[str]] | None:
        """Probe one host:port for an LLM HTTP API.

        Returns ``(kind, [model_ids])`` for an OpenAI-compatible ``/v1/models``
        or an Ollama ``/api/tags`` endpoint, or None if neither answers.
        """
        base = f"http://{host}:{port}"
        client = httpx.Client(timeout=timeout)
        try:
            res = client.get(f"{base}/v1/models")
            if res.status_code == 200:
                models = [
                    item.get("id")
                    for item in res.json().get("data", [])
                    if isinstance(item, dict) and item.get("id")
                ]
                if models:
                    return "OpenAI-compatible", models

            res = client.get(f"{base}/api/tags")
            if res.status_code == 200:
                models = [
                    item.get("name")
                    for item in res.json().get("models", [])
                    if isinstance(item, dict) and item.get("name")
                ]
                if models:
                    return "Ollama", models
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        finally:
            client.close()
        return None

    @classmethod
    def _make_lan_config(cls, host: str, port: int, model_id: str, label: str) -> LLMBackendConfig:
        """Build an LLMBackendConfig named after its LAN host:port."""
        clean_host = re.sub(r"[^A-Za-z0-9_-]", "_", host.rstrip("."))
        clean_model = model_id.replace(":", "_").replace("/", "_")
        key = f"auto_lan_{clean_host}_{port}_{clean_model}"
        name = f"⚡ {label} (LAN {host}:{port}): {model_id}"
        return LLMBackendConfig(
            key=key,
            name=name,
            base_url=f"http://{host}:{port}/v1",
            model=model_id,
            api_key="ollama",
        )

    @classmethod
    def get_available_backends(cls, autodiscover: bool = False) -> dict[str, LLMBackendConfig]:
        """Discover available LLM backends defined in environment variables.

        Supports multi-backend configuration via LLM_BACKENDS (comma-separated list of keys,
        e.g. `local, gemini`) with per-backend settings `LLM_BACKEND_<KEY>_*`.

        If `autodiscover` is True, also probes running local servers (Ollama,
        llama-server, etc.) and browses the LAN via mDNS/ZeroConf for LLM hosts
        on other devices.

        The heavyweight /24 subnet scan is opt-in: enable it with the
        `LLM_LAN_SCAN` env var (`1`/`true`/`yes`) and optionally restrict it to
        specific hosts with `LLM_LAN_HOSTS` (comma-separated IPs or hostnames).

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
            backends.update(cls.discover_lan_backends())
            if os.getenv("LLM_LAN_SCAN", "0").strip().lower() in ("1", "true", "yes"):
                static_hosts = [
                    h.strip() for h in os.getenv("LLM_LAN_HOSTS", "").split(",") if h.strip()
                ]
                backends.update(cls.scan_lan_ports(hosts=static_hosts or None))

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
    def save_settings_to_env(
        cls,
        backend_key: str,
        config: LLMBackendConfig | None = None,
        env_path: str = ".env",
    ) -> None:
        """Save selected default LLM backend and any autodiscovered backend details to .env."""
        path = Path(env_path)
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()

        updates: dict[str, str] = {
            "LLM_DEFAULT_BACKEND": backend_key,
        }

        # If backend config is provided or key is auto-discovered, update backend definitions
        if config or backend_key.startswith("auto_"):
            cfg = config
            if cfg is None:
                available = cls.get_available_backends(autodiscover=True)
                cfg = available.get(backend_key)

            if cfg:
                key_upper = backend_key.upper()
                updates[f"LLM_BACKEND_{key_upper}_NAME"] = (
                    f'"{cfg.name}"' if not cfg.name.startswith('"') else cfg.name
                )
                updates[f"LLM_BACKEND_{key_upper}_BASE_URL"] = cfg.base_url
                updates[f"LLM_BACKEND_{key_upper}_MODEL"] = cfg.model
                updates[f"LLM_BACKEND_{key_upper}_API_KEY"] = cfg.api_key
                updates[f"LLM_BACKEND_{key_upper}_TIMEOUT"] = str(cfg.timeout)

                # Ensure backend_key is listed in LLM_BACKENDS
                current_backends: list[str] = []
                for line in lines:
                    if line.strip().startswith("LLM_BACKENDS="):
                        val = line.split("=", 1)[1].strip()
                        current_backends = [k.strip() for k in val.split(",") if k.strip()]
                        break

                if not current_backends and os.getenv("LLM_BACKENDS"):
                    current_backends = [
                        k.strip() for k in os.getenv("LLM_BACKENDS", "").split(",") if k.strip()
                    ]

                if backend_key not in current_backends:
                    current_backends.append(backend_key)
                    updates["LLM_BACKENDS"] = ",".join(current_backends)

        # Apply all updates to existing lines or append new ones
        updated_keys: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            matched_key = None
            for k in updates:
                if stripped.startswith(f"{k}=") or stripped.startswith(f"{k} ="):
                    matched_key = k
                    break

            if matched_key:
                new_lines.append(f"{matched_key}={updates[matched_key]}")
                updated_keys.add(matched_key)
                os.environ[matched_key] = updates[matched_key].strip('"')
            else:
                new_lines.append(line)

        for k, v in updates.items():
            if k not in updated_keys:
                new_lines.append(f"{k}={v}")
                os.environ[k] = v.strip('"')

        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    @staticmethod
    def _block_bounds(block: Any) -> tuple[str, str]:
        """Return ``(start, end)`` for a time block, object- or mapping-shaped.

        Free blocks arrive as :class:`TimeBlock` objects from the GCal service but
        as plain dicts when they have made a round trip through JSON (API, cache,
        Streamlit). Both must work — silently reading neither is how capacity
        quietly became zero.
        """
        if isinstance(block, Mapping):
            return str(block.get("start", "")), str(block.get("end", ""))
        return str(getattr(block, "start", "") or ""), str(getattr(block, "end", "") or "")

    @classmethod
    def _compute_input_hash(
        cls,
        tasks: list[Task],
        free_blocks: list[Any],
        reference_date: date | None = None,
        mode: TriageMode = "balanced",
    ) -> str:
        """Compute a deterministic SHA-256 hash of the input payload."""
        ref = (reference_date or date.today()).isoformat()
        task_data = sorted(
            [
                f"{t.id}:{t.content}:{t.due_date}:{t.project_name or ''}:{','.join(t.labels)}"
                for t in tasks
            ]
        )
        block_data = sorted(f"{start}:{end}" for start, end in map(cls._block_bounds, free_blocks))
        payload = json.dumps(
            {"ref": ref, "tasks": task_data, "blocks": block_data, "mode": mode},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _calculate_free_minutes(cls, free_blocks: list[Any]) -> int:
        """Calculate the total free minutes across provided time blocks.

        A block that cannot be parsed is skipped *and logged* — reporting zero
        capacity to the LLM without a trace makes it silently plan a day that
        looks fully booked.
        """
        total = 0
        for block in free_blocks:
            start_str, end_str = cls._block_bounds(block)
            if not start_str or not end_str:
                logger.warning("Skipping free block with missing bounds: %r", block)
                continue
            try:
                start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except ValueError:
                logger.warning("Skipping unparseable free block %s → %s", start_str, end_str)
                continue

            # Mixing naive and aware values raises; assume the naive side shares
            # the other side's offset rather than dropping the block.
            if (start.tzinfo is None) != (end.tzinfo is None):
                if start.tzinfo is None:
                    start = start.replace(tzinfo=end.tzinfo)
                else:
                    end = end.replace(tzinfo=start.tzinfo)

            minutes = int((end - start).total_seconds() // 60)
            if minutes > 0:
                total += minutes
        return total

    def plan_triage(
        self,
        tasks: list[Task],
        free_blocks: list[Any],
        *,
        mode: TriageMode = "balanced",
        time_block: bool = False,
        force_refresh: bool = False,
        reference_date: date | None = None,
    ) -> TriagePlan:
        """Ask the LLM to propose a 1-3-5 plan for today (uses disk cache if payload matches)."""
        payload_hash = self._compute_input_hash(tasks, free_blocks, reference_date, mode=mode)
        cache_file = self._cache_dir / f"triage_{payload_hash}.json" if self._cache_dir else None

        if not force_refresh and cache_file and cache_file.exists():
            try:
                cached_data = cache_file.read_text(encoding="utf-8")
                plan = TriagePlan.model_validate_json(cached_data)
                if time_block and plan.schedule is None and free_blocks:
                    plan.schedule = self.schedule_time_blocks(plan, free_blocks, mode=mode)
                    # Persist pass 2 as well, or every later run pays for it again.
                    self._write_cache(cache_file, plan)
                return plan
            except Exception:
                pass  # Fallback to fresh LLM call if cache reading fails

        prompt = self._build_prompt(tasks, free_blocks, mode=mode)
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
                        "Valitse 1 Big, 3 Medium, 5 Small -tehtävää valitun moodin mukaan.\n"
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
            mode=mode,
        )

        if time_block and free_blocks:
            hydrated_plan.schedule = self.schedule_time_blocks(
                hydrated_plan, free_blocks, mode=mode
            )

        self._write_cache(cache_file, hydrated_plan)

        return hydrated_plan

    @staticmethod
    def _write_cache(cache_file: Path | None, plan: TriagePlan) -> None:
        """Best-effort plan persistence; a broken cache must never fail a run."""
        if cache_file is None:
            return
        try:
            cache_file.write_text(plan.model_dump_json(), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write triage cache %s: %s", cache_file, exc)

    def schedule_time_blocks(
        self,
        plan: TriagePlan,
        free_blocks: list[Any],
        mode: TriageMode = "balanced",
    ) -> DailySchedule:
        """Pass 2: Map selected tasks into free calendar blocks to form a schedule."""
        total_mins = self._calculate_free_minutes(free_blocks)
        selected_tasks = plan.big + plan.medium + plan.small

        task_lines = "\n".join(
            f"- [{t.id}] {t.content} (duration: {t.duration_minutes or 30}m)"
            for t in selected_tasks
        )
        block_lines = "\n".join(
            f"- {start} to {end}" for start, end in map(self._block_bounds, free_blocks)
        )

        prompt = (
            f"Mode: {mode.upper()}\n"
            f"Total Free Focus Capacity: {total_mins} minutes.\n\n"
            f"Selected Triage Tasks:\n{task_lines}\n\n"
            f"Available Time Blocks:\n{block_lines}\n\n"
            "Fit the selected tasks into the available free time blocks. "
            "Return a structured DailySchedule containing slot assignments, "
            "total planned minutes, and whether capacity is exceeded."
        )

        schedule: DailySchedule = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert time-blocking assistant. Map the given tasks "
                        "into available free calendar blocks. Be realistic with durations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_model=DailySchedule,
            max_tokens=self._max_tokens,
        )
        return schedule

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
        project_str = f" [project: {t.project_name}]" if t.project_name else ""
        label_str = f" [labels: {', '.join(t.labels)}]" if t.labels else ""
        details = f"(due: {t.due_date}{overdue_str}, priority: {prio_str}{dur_str})"
        info = f"{details}{project_str}{label_str}"
        return f"- [{t.id}] {t.content} {info}"

    @classmethod
    def _build_prompt(
        cls, tasks: list[Task], free_blocks: list[Any], mode: TriageMode = "balanced"
    ) -> str:
        task_lines = "\n".join(cls._format_task(t) for t in tasks) or "(no tasks due today)"
        total_mins = cls._calculate_free_minutes(free_blocks)

        block_lines = (
            "\n".join(f"- {start} to {end}" for start, end in map(cls._block_bounds, free_blocks))
            or "(no free blocks identified)"
        )
        capacity_str = (
            f"Total free capacity: {total_mins} mins"
            if total_mins > 0
            else "No free blocks identified"
        )

        return (
            f"Triage Mode: {mode.upper()}\n"
            f"Calendar Capacity: {capacity_str}\n\n"
            f"Today's tasks:\n{task_lines}\n\n"
            f"Free time blocks:\n{block_lines}\n\n"
            "Propose a 1-3-5 plan for today according to the requested mode "
            "and list remaining tasks under postponed."
        )
