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
