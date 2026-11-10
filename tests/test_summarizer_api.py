from __future__ import annotations

from pathlib import Path


def test_fetch_ai_summaries_returns_empty_for_skeleton_only_modes(monkeypatch, tmp_path):
    from codemerge.llm import summarize as api_mod
    from codemerge.llm import _summarize_service as service_mod

    class StubFileProcessor:
        def read_file_content(self, path: str) -> str:
            return Path(path).read_text(encoding="utf-8")

        def count_tokens(self, text: str) -> int:
            return len(text.split())

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    monkeypatch.setattr(
        service_mod,
        "get_groq_client",
        lambda: (_ for _ in ()).throw(AssertionError("provider must not be requested")),
    )

    file_path = tmp_path / "demo.py"
    file_path.write_text("def demo():\n    return 1\n", encoding="utf-8")

    for mode in ("skeleton_only", "code_plus_skeleton"):
        summaries, errors = api_mod.fetch_ai_summaries(
            [str(file_path)],
            StubFileProcessor(),
            mode,
            app_config={},
            use_batch=False,
        )
        assert summaries == {}
        assert errors == []


def test_fetch_ai_summaries_falls_back_when_batch_unavailable(monkeypatch, tmp_path):
    from codemerge.llm import summarize as api_mod
    from codemerge.llm import _summarize_service as service_mod

    class StubFileProcessor:
        def read_file_content(self, path: str) -> str:
            return "print('x')"

        def count_tokens(self, text: str) -> int:
            return len(text.split())

    class StubLimiter:
        def __init__(self) -> None:
            self.tokens: list[int] = []

        def acquire(self, tokens: int) -> None:
            self.tokens.append(tokens)

    class StubResponses:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.calls.append(kwargs)

            class Resp:
                output_text = "demo module summary"

            return Resp()

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = StubResponses(self)

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    client = StubClient()
    monkeypatch.setattr(service_mod, "get_groq_client", lambda: client)
    monkeypatch.setattr(service_mod, "supports_batch_api", lambda: False)
    monkeypatch.setattr(service_mod, "get_rate_limiter", lambda *_args, **_kwargs: StubLimiter())

    file_path = tmp_path / "demo.py"
    file_path.write_text("print('x')\n", encoding="utf-8")

    summaries, errors = api_mod.fetch_ai_summaries(
        [str(file_path)],
        StubFileProcessor(),
        "short_only",
        app_config={},
        use_batch=True,
    )

    assert errors == []
    assert str(file_path) in summaries
    assert summaries[str(file_path)] == "demo module summary"
    assert client.calls, "threaded path should invoke client.responses.create"
    assert client.calls[0]["timeout"] == 120.0


def test_multi_chunk_runs_unifier(monkeypatch, tmp_path):
    from codemerge.llm import summarize as api_mod
    from codemerge.llm import _summarize_service as service_mod

    class StubFileProcessor:
        def read_file_content(self, path: str) -> str:
            return Path(path).read_text(encoding="utf-8")

        def count_tokens(self, text: str) -> int:
            return len(text)

    class StubLimiter:
        rpm = 1000
        tpm = 1_000_000
        context_window = 131_072

        def acquire(self, _tokens: int) -> None:
            return None

    class StubResponses:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.calls.append(kwargs)

            class Resp:
                output_text = "chunk summary"

            return Resp()

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = StubResponses(self)

    unifier_calls: list[str] = []

    def fake_fetch_ai_text_summary(*, prompt_text: str, **_kwargs) -> str:
        unifier_calls.append(prompt_text)
        return "unified summary"

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    monkeypatch.setattr(service_mod, "get_groq_client", lambda: StubClient())
    monkeypatch.setattr(service_mod, "supports_batch_api", lambda: False)
    monkeypatch.setattr(service_mod, "get_rate_limiter", lambda *_args, **_kwargs: StubLimiter())
    monkeypatch.setattr(service_mod, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    file_path = tmp_path / "demo.py"
    file_path.write_text("x" * 50000, encoding="utf-8")

    summaries, errors, debug = api_mod.fetch_ai_summaries(
        [str(file_path)],
        StubFileProcessor(),
        "short_only",
        app_config={},
        max_workers=1,
        return_debug=True,
    )

    assert errors == []
    assert unifier_calls
    info = debug[str(file_path)]
    assert info["chunk_count"] > 1
    assert info["unifier_ran"] is True
    assert summaries[str(file_path)] == "unified summary"


def test_fetch_ai_text_summary_passes_default_and_custom_request_timeout(monkeypatch):
    from codemerge.llm import _summarize_service as service_mod

    class StubLimiter:
        def acquire(self, _tokens: int) -> None:
            return None

    class StubResponses:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.calls.append(kwargs)

            class Resp:
                output_text = "aggregate summary"

            return Resp()

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = StubResponses(self)

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    client = StubClient()
    monkeypatch.setattr(service_mod, "get_groq_client", lambda: client)
    monkeypatch.setattr(service_mod, "get_rate_limiter", lambda *_args, **_kwargs: StubLimiter())

    default_result = service_mod.fetch_ai_text_summary(
        prompt_text="hello",
        app_config={},
        token_counter=lambda text: max(len(text), 1),
        log_context={"phase": "quick"},
    )
    custom_result = service_mod.fetch_ai_text_summary(
        prompt_text="hello again",
        app_config={"groq_request_timeout_seconds": 5.5},
        token_counter=lambda text: max(len(text), 1),
        log_context={"phase": "quick"},
    )

    assert default_result == "aggregate summary"
    assert custom_result == "aggregate summary"
    assert client.calls[0]["timeout"] == 120.0
    assert client.calls[1]["timeout"] == 5.5


def test_compare_files_ai_uses_configured_request_timeout(monkeypatch):
    from codemerge.llm import _summarize_service as service_mod

    class StubResponses:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.calls.append(kwargs)

            class Resp:
                output_text = "USE_A"

            return Resp()

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = StubResponses(self)

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    client = StubClient()
    monkeypatch.setattr(service_mod, "get_groq_client", lambda: client)

    verdict = service_mod.compare_files_ai(
        "alpha",
        "beta",
        app_config={"groq_request_timeout_seconds": 7.25},
    )

    assert verdict == "USE_A"
    assert client.calls[0]["timeout"] == 7.25


def test_fetch_ai_text_summary_retries_transient_provider_errors(monkeypatch):
    from codemerge.llm import _summarize_service as service_mod

    class StubLimiter:
        def acquire(self, _tokens: int) -> None:
            return None

    class StubResponses:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.calls.append(kwargs)
            if len(self._parent.calls) == 1:
                raise RuntimeError("service unavailable")

            class Resp:
                output_text = "aggregate summary"

            return Resp()

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = StubResponses(self)

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    monkeypatch.setattr(service_mod, "get_rate_limiter", lambda *_args, **_kwargs: StubLimiter())
    monkeypatch.setattr(service_mod.time, "sleep", lambda *_args, **_kwargs: None)
    client = StubClient()
    monkeypatch.setattr(service_mod, "get_groq_client", lambda: client)

    result = service_mod.fetch_ai_text_summary(
        prompt_text="hello",
        app_config={"groq_summary_retry_budget": 1},
        token_counter=lambda text: max(len(text), 1),
        log_context={"phase": "quick"},
    )

    assert result == "aggregate summary"
    assert len(client.calls) == 2


def test_fetch_ai_summaries_retries_transient_file_errors(monkeypatch, tmp_path):
    from codemerge.llm import summarize as api_mod
    from codemerge.llm import _summarize_service as service_mod

    class StubFileProcessor:
        def read_file_content(self, path: str) -> str:
            return Path(path).read_text(encoding="utf-8")

        def count_tokens(self, text: str) -> int:
            return max(len(text.split()), 1)

    class StubLimiter:
        def acquire(self, _tokens: int) -> None:
            return None

    class StubResponses:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.calls.append(kwargs)
            if len(self._parent.calls) == 1:
                raise RuntimeError("connection reset")

            class Resp:
                output_text = "file summary"

            return Resp()

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = StubResponses(self)

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    monkeypatch.setattr(service_mod, "supports_batch_api", lambda: False)
    monkeypatch.setattr(service_mod, "get_rate_limiter", lambda *_args, **_kwargs: StubLimiter())
    monkeypatch.setattr(service_mod.time, "sleep", lambda *_args, **_kwargs: None)
    client = StubClient()
    monkeypatch.setattr(service_mod, "get_groq_client", lambda: client)

    file_path = tmp_path / "demo.py"
    file_path.write_text("print('x')\n", encoding="utf-8")

    summaries, errors = api_mod.fetch_ai_summaries(
        [str(file_path)],
        StubFileProcessor(),
        "short_only",
        app_config={"groq_summary_retry_budget": 1, "groq_request_timeout_seconds": 4.0},
        max_workers=1,
    )

    assert errors == []
    assert summaries[str(file_path)] == "file summary"
    assert len(client.calls) == 2
    assert client.calls[0]["timeout"] == 4.0
    assert client.calls[1]["timeout"] == 4.0


def test_fetch_ai_summaries_retries_tool_use_failed_file_errors(monkeypatch, tmp_path):
    from codemerge.llm import summarize as api_mod
    from codemerge.llm import _summarize_service as service_mod

    class StubFileProcessor:
        def read_file_content(self, path: str) -> str:
            return Path(path).read_text(encoding="utf-8")

        def count_tokens(self, text: str) -> int:
            return max(len(text.split()), 1)

    class StubLimiter:
        def acquire(self, _tokens: int) -> None:
            return None

    class ToolUseFailed(RuntimeError):
        status_code = 400

    class StubResponses:
        def __init__(self, parent):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.calls.append(kwargs)
            if len(self._parent.calls) < 3:
                raise ToolUseFailed(
                    "Error code: 400 - {'error': {'message': 'Tool choice is none, but model called a tool', "
                    "'type': 'invalid_request_error', 'code': 'tool_use_failed'}}"
                )

            class Resp:
                output_text = "file summary"

            return Resp()

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = StubResponses(self)

    monkeypatch.setattr(service_mod, "groq_available", lambda: True)
    monkeypatch.setattr(service_mod, "supports_batch_api", lambda: False)
    monkeypatch.setattr(service_mod, "get_rate_limiter", lambda *_args, **_kwargs: StubLimiter())
    monkeypatch.setattr(service_mod.time, "sleep", lambda *_args, **_kwargs: None)
    client = StubClient()
    monkeypatch.setattr(service_mod, "get_groq_client", lambda: client)

    file_path = tmp_path / "demo.py"
    file_path.write_text("print('x')\n", encoding="utf-8")

    summaries, errors = api_mod.fetch_ai_summaries(
        [str(file_path)],
        StubFileProcessor(),
        "short_only",
        app_config={"groq_summary_retry_budget": 2},
        max_workers=1,
    )

    assert errors == []
    assert summaries[str(file_path)] == "file summary"
    assert len(client.calls) == 3


def test_app_config_surfaces_summary_retry_budget_default(tmp_path):
    from codemerge.filemerger.config import AppConfig

    cfg = AppConfig(config_file=str(tmp_path / "config.json"))

    assert cfg.config["groq_request_timeout_seconds"] == 120.0
    assert cfg.config["groq_summary_retry_budget"] == 2
