"""Tests for the LLM triage service."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from models.task import LLMBackendConfig, Task, TriagePlan, TriagePlanIDs
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
        assert call_kwargs.get("max_tokens") == 8192

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
        monkeypatch.delenv("LLM_BACKENDS", raising=False)
        monkeypatch.delenv("LLM_DEFAULT_BACKEND", raising=False)
        monkeypatch.setenv(
            "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        monkeypatch.setenv("LLM_MODEL", "gemini-2.0-flash")
        monkeypatch.setenv("LLM_API_KEY", "AIzaSy-test-key")

        service = LLMService.from_env()
        assert service._model == "gemini-2.0-flash"

    def test_from_env_falls_back_to_ollama_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLMService.from_env() falls back to OLLAMA_* when LLM_* are unset."""
        monkeypatch.delenv("LLM_BACKENDS", raising=False)
        monkeypatch.delenv("LLM_DEFAULT_BACKEND", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:8000/v1")
        monkeypatch.setenv("OLLAMA_MODEL", "gemma4")

        service = LLMService.from_env()
        assert service._model == "gemma4"

    def test_get_available_backends_parses_custom_backends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify LLMService parses multiple LLM backends defined in env vars."""
        monkeypatch.setenv("LLM_BACKENDS", "local, gemini")
        monkeypatch.setenv("LLM_DEFAULT_BACKEND", "gemini")
        monkeypatch.setenv("LLM_BACKEND_LOCAL_NAME", "Local Gemma 12B")
        monkeypatch.setenv("LLM_BACKEND_LOCAL_BASE_URL", "http://127.0.0.1:8000/v1")
        monkeypatch.setenv("LLM_BACKEND_LOCAL_MODEL", "gemma4")
        monkeypatch.setenv("LLM_BACKEND_GEMINI_NAME", "Google Gemini 3.5 Flash Lite")
        monkeypatch.setenv(
            "LLM_BACKEND_GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        monkeypatch.setenv("LLM_BACKEND_GEMINI_MODEL", "gemini-3.5-flash-lite")
        monkeypatch.setenv("LLM_BACKEND_GEMINI_API_KEY", "test-gemini-key")

        backends = LLMService.get_available_backends()
        assert "local" in backends
        assert "gemini" in backends
        assert backends["local"].name == "Local Gemma 12B"
        assert backends["gemini"].model == "gemini-3.5-flash-lite"

        # Test selecting specific backend
        gemini_svc = LLMService.from_env(backend_key="gemini")
        assert gemini_svc._model == "gemini-3.5-flash-lite"

        # Test default backend selection
        default_svc = LLMService.from_env()
        assert default_svc._model == "gemini-3.5-flash-lite"

    def test_format_task_includes_labels_and_project_name(self) -> None:
        task = Task(
            id="t1",
            content="Build feature",
            project_id="p1",
            project_name="Backend System",
            labels=["@deepwork", "@urgent"],
            due_date=date(2026, 8, 15),
        )
        formatted = LLMService._format_task(task)
        assert "[project: Backend System]" in formatted
        assert "[labels: @deepwork, @urgent]" in formatted

    def test_build_prompt_includes_capacity_and_mode(self) -> None:
        from models.task import TimeBlock

        tasks = [_task(id="1", content="Write report")]
        blocks = [
            TimeBlock(
                start="2026-08-15T09:00:00+00:00", end="2026-08-15T11:00:00+00:00"
            ),  # 120 min
            TimeBlock(start="2026-08-15T13:00:00+00:00", end="2026-08-15T14:00:00+00:00"),  # 60 min
        ]
        prompt = LLMService._build_prompt(tasks, blocks, mode="deep_work")
        assert "Total free capacity: 180 mins" in prompt
        assert "Triage Mode: DEEP_WORK" in prompt

    def test_schedule_time_blocks_returns_daily_schedule(self) -> None:
        from models.task import DailySchedule, ScheduledSlot, TimeBlock

        tasks = [_task(id="1", content="Write report")]
        blocks = [TimeBlock(start="2026-08-15T09:00:00+00:00", end="2026-08-15T10:30:00+00:00")]

        plan = TriagePlan(big=tasks, medium=[], small=[], postponed=[])
        fake_schedule = DailySchedule(
            slots=[
                ScheduledSlot(
                    task_id="1",
                    task_content="Write report",
                    start_time="09:00",
                    end_time="10:30",
                    category="big",
                    notes="Focused block",
                )
            ],
            total_planned_minutes=90,
            is_overcapacity=False,
            summary="Single big task scheduled.",
        )

        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        fake_client.chat.completions.create.return_value = fake_schedule

        with self._patch_instructor(fake_instructor):
            schedule = self._service().schedule_time_blocks(plan=plan, free_blocks=blocks)

        assert isinstance(schedule, DailySchedule)
        assert len(schedule.slots) == 1
        assert schedule.slots[0].task_id == "1"

    def test_plan_triage_with_time_block_runs_second_pass(self) -> None:
        from models.task import DailySchedule, ScheduledSlot, TimeBlock

        tasks = [_task(id="1", content="Write report")]
        blocks = [TimeBlock(start="2026-08-15T09:00:00+00:00", end="2026-08-15T10:30:00+00:00")]

        raw_plan = TriagePlanIDs(big=["1"])
        fake_schedule = DailySchedule(
            slots=[
                ScheduledSlot(
                    task_id="1",
                    task_content="Write report",
                    start_time="09:00",
                    end_time="10:30",
                    category="big",
                )
            ],
            total_planned_minutes=90,
        )

        fake_instructor = self._fake_instructor()
        fake_client = fake_instructor.from_openai.return_value
        # Side effect for first pass (TriagePlanIDs) and second pass (DailySchedule)
        fake_client.chat.completions.create.side_effect = [raw_plan, fake_schedule]

        with self._patch_instructor(fake_instructor):
            plan = self._service().plan_triage(
                tasks=tasks, free_blocks=blocks, mode="deep_work", time_block=True
            )

        assert plan.schedule is not None
        assert len(plan.schedule.slots) == 1
        assert plan.schedule.slots[0].task_id == "1"

    def test_discover_local_backends_finds_openai_compatible_models(self) -> None:
        """Verify discover_local_backends identifies local models from /v1/models endpoint."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"id": "gemma-4-12b-it.gguf"}, {"id": "llama-3.2-3b"}]
        }

        with patch("httpx.Client.get", return_value=mock_response):
            discovered = LLMService.discover_local_backends(target_ports=[8000])

        assert len(discovered) == 2
        keys = list(discovered.keys())
        assert (
            "auto_8000_gemma-4-12b-it.gguf" in keys
            or "auto_8000_gemma_4_12b_it_gguf" in keys
            or any("gemma" in k for k in keys)
        )
        found_cfg = next(c for c in discovered.values() if c.model == "llama-3.2-3b")
        assert found_cfg.base_url == "http://localhost:8000/v1"

    def test_discover_local_backends_finds_ollama_tags(self) -> None:
        """Verify discover_local_backends identifies local models from Ollama /api/tags endpoint."""

        def mock_get(url: str, **kwargs: object) -> MagicMock:
            res = MagicMock()
            if "/api/tags" in url:
                res.status_code = 200
                res.json.return_value = {"models": [{"name": "qwen2.5-coder:latest"}]}
            else:
                res.status_code = 404
                res.json.return_value = {}
            return res

        with patch("httpx.Client.get", side_effect=mock_get):
            discovered = LLMService.discover_local_backends(target_ports=[11434])

        found_cfg = next((c for c in discovered.values() if "qwen" in c.model), None)
        assert found_cfg is not None
        assert found_cfg.base_url == "http://localhost:11434/v1"

    def test_discover_local_backends_handles_connection_error(self) -> None:
        """Verify network errors during discovery are caught gracefully."""
        import httpx

        with patch("httpx.Client.get", side_effect=httpx.ConnectError("Connection refused")):
            discovered = LLMService.discover_local_backends(target_ports=[9999])

        assert discovered == {}

    def test_get_available_backends_with_autodiscover(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify get_available_backends(autodiscover=True) merges env and discovered backends."""
        monkeypatch.setenv("LLM_BACKENDS", "local")
        monkeypatch.setenv("LLM_BACKEND_LOCAL_NAME", "Local Gemma")

        mock_discovered = {
            "auto_11434_ollama": LLMBackendConfig(
                key="auto_11434_ollama",
                name="[Discovered 11434] ollama:latest",
                base_url="http://localhost:11434/v1",
                model="ollama:latest",
            )
        }

        with patch.object(LLMService, "discover_local_backends", return_value=mock_discovered):
            backends = LLMService.get_available_backends(autodiscover=True)

        assert "local" in backends
        assert "auto_11434_ollama" in backends

    def test_validate_backend_success(self) -> None:
        cfg = LLMBackendConfig(
            key="test", name="Test LLM", base_url="http://localhost:8000/v1", model="test"
        )
        mock_res = MagicMock(status_code=200)
        with patch("httpx.Client.get", return_value=mock_res):
            ok, msg = LLMService.validate_backend(cfg)
            assert ok is True
            assert "connected" in msg.lower() or "200" in msg

    def test_validate_backend_auth_failed(self) -> None:
        cfg = LLMBackendConfig(
            key="test", name="Test LLM", base_url="http://localhost:8000/v1", model="test"
        )
        mock_res = MagicMock(status_code=401)
        with patch("httpx.Client.get", return_value=mock_res):
            ok, msg = LLMService.validate_backend(cfg)
            assert ok is False
            assert "api key" in msg.lower() or "401" in msg

    def test_validate_backend_unreachable(self) -> None:
        cfg = LLMBackendConfig(
            key="test", name="Test LLM", base_url="http://localhost:8000/v1", model="test"
        )
        with patch("httpx.Client.get", side_effect=Exception("Connection refused")):
            ok, msg = LLMService.validate_backend(cfg)
            assert ok is False
            assert "unreachable" in msg.lower() or "connection refused" in msg.lower()

    def test_save_settings_to_env_updates_default_backend(self, tmp_path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_BACKENDS=local,gemini\nLLM_DEFAULT_BACKEND=local\n")

        LLMService.save_settings_to_env(backend_key="gemini", env_path=str(env_file))

        content = env_file.read_text()
        assert "LLM_DEFAULT_BACKEND=gemini" in content
        assert "LLM_DEFAULT_BACKEND=local" not in content

    def test_save_settings_to_env_saves_autodiscovered_backend(self, tmp_path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_BACKENDS=local,gemini\nLLM_DEFAULT_BACKEND=local\n")

        cfg = LLMBackendConfig(
            key="auto_11434_llama3",
            name="⚡ Discovered Ollama (Port 11434): llama3",
            base_url="http://localhost:11434/v1",
            model="llama3",
            api_key="ollama",
        )

        LLMService.save_settings_to_env(
            backend_key="auto_11434_llama3", config=cfg, env_path=str(env_file)
        )

        content = env_file.read_text()
        assert "LLM_DEFAULT_BACKEND=auto_11434_llama3" in content
        assert "auto_11434_llama3" in content
        assert "LLM_BACKEND_AUTO_11434_LLAMA3_MODEL=llama3" in content
        assert "LLM_BACKEND_AUTO_11434_LLAMA3_BASE_URL=http://localhost:11434/v1" in content


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


class TestLanDiscovery:
    """LAN LLM discovery via mDNS/ZeroConf and subnet scanning."""

    def test_discover_lan_backends_finds_mdns_service(self) -> None:
        """mDNS browsing finds an Ollama host on the LAN and enumerates its models."""
        from services import llm_service as mod

        fake_info = MagicMock()
        fake_info.parsed_addresses.return_value = ["192.168.1.50"]
        fake_info.port = 11434

        fake_zc = MagicMock()
        fake_zc.get_service_info.return_value = fake_info

        def fake_browser_init(zc, service_types, handlers):
            handlers[0](
                zc,
                "_ollama._tcp.local.",
                "ollama-50._ollama._tcp.local.",
                mod.ServiceStateChange.Added,
            )
            return MagicMock()

        def mock_probe(host: str, port: int, timeout: float = 2.0):
            return ("Ollama", ["qwen2.5:latest"])

        with (
            patch("services.llm_service.Zeroconf", return_value=fake_zc),
            patch("services.llm_service.ServiceBrowser", side_effect=fake_browser_init),
            patch("services.llm_service.time.sleep"),
            patch.object(LLMService, "_probe_host_models", side_effect=mock_probe),
        ):
            discovered = LLMService.discover_lan_backends(use_cache=False, probe_timeout=1.0)

        assert discovered
        cfg = next(iter(discovered.values()))
        assert cfg.base_url == "http://192.168.1.50:11434/v1"
        assert cfg.model == "qwen2.5:latest"
        assert cfg.key.startswith("auto_lan_192_168_1_50_11434_")

    def test_discover_lan_backends_skips_services_without_llm_api(self) -> None:
        """mDNS services that do not expose an LLM HTTP API are ignored."""
        from services import llm_service as mod

        fake_info = MagicMock()
        fake_info.parsed_addresses.return_value = ["192.168.1.50"]
        fake_info.port = 8000

        fake_zc = MagicMock()
        fake_zc.get_service_info.return_value = fake_info

        def fake_browser_init(zc, service_types, handlers):
            handlers[0](
                zc, "_http._tcp.local.", "printer._http._tcp.local.", mod.ServiceStateChange.Added
            )
            return MagicMock()

        with (
            patch("services.llm_service.Zeroconf", return_value=fake_zc),
            patch("services.llm_service.ServiceBrowser", side_effect=fake_browser_init),
            patch("services.llm_service.time.sleep"),
            patch.object(LLMService, "_probe_host_models", return_value=None),
        ):
            discovered = LLMService.discover_lan_backends(use_cache=False)

        assert discovered == {}

    def test_discover_lan_backends_graceful_when_zeroconf_missing(self) -> None:
        """Discovery degrades to empty results when the zeroconf library is unavailable."""
        with (
            patch("services.llm_service.ServiceBrowser", None),
            patch("services.llm_service.Zeroconf", None),
        ):
            discovered = LLMService.discover_lan_backends(use_cache=False)
        assert discovered == {}

    def test_scan_lan_ports_finds_openai_compatible_host(self) -> None:
        """Subnet scan finds an OpenAI-compatible server on an explicitly given host."""

        def fake_connect(host: str, port: int, timeout: float = 0.5) -> bool:
            return host == "192.168.1.77" and port == 8000

        with (
            patch.object(LLMService, "_try_connect", side_effect=fake_connect),
            patch.object(
                LLMService,
                "_probe_host_models",
                return_value=("OpenAI", ["gemma-4-12b-it.gguf"]),
            ),
        ):
            discovered = LLMService.scan_lan_ports(
                hosts=["192.168.1.77", "192.168.1.78"],
                ports=[11434, 8000],
                timeout=0.2,
                max_workers=4,
            )

        found = next((c for c in discovered.values() if c.model == "gemma-4-12b-it.gguf"), None)
        assert found is not None
        assert found.base_url == "http://192.168.1.77:8000/v1"
        assert found.key.startswith("auto_lan_192_168_1_77_8000_")

    def test_scan_lan_ports_returns_empty_when_no_hosts_reachable(self) -> None:
        """Subnet scan returns nothing when no hosts accept TCP connections."""

        def fake_connect(host: str, port: int, timeout: float = 0.5) -> bool:
            return False

        with patch.object(LLMService, "_try_connect", side_effect=fake_connect):
            discovered = LLMService.scan_lan_ports(
                hosts=["192.168.1.99"],
                ports=[11434, 8000],
                timeout=0.2,
                max_workers=4,
            )
        assert discovered == {}

    def test_get_available_backends_autodiscover_merges_lan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """autodiscover=True merges local, mDNS LAN, and env-configured backends."""
        monkeypatch.setenv("LLM_BACKENDS", "local")
        monkeypatch.setenv("LLM_BACKEND_LOCAL_NAME", "Local Gemma")

        lan_cfg = LLMBackendConfig(
            key="auto_lan_192_168_1_50_11434_qwen",
            name="Ollama (LAN 192.168.1.50:11434): qwen2.5:latest",
            base_url="http://192.168.1.50:11434/v1",
            model="qwen2.5:latest",
        )

        with (
            patch.object(LLMService, "discover_local_backends", return_value={}),
            patch.object(
                LLMService,
                "discover_lan_backends",
                return_value={"auto_lan_192_168_1_50_11434_qwen": lan_cfg},
            ),
            patch.object(LLMService, "scan_lan_ports", return_value={}),
        ):
            backends = LLMService.get_available_backends(autodiscover=True)

        assert "local" in backends
        assert "auto_lan_192_168_1_50_11434_qwen" in backends
        assert (
            backends["auto_lan_192_168_1_50_11434_qwen"].base_url == "http://192.168.1.50:11434/v1"
        )
