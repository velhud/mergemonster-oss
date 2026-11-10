import os
import types
import concurrent.futures
from codemerge.core.tokens import get_encoding
from codemerge.llm import summarize as summarizer
from codemerge.llm import _summarize_service as summary_service

encoder = get_encoding()

class DummyFP:
    _encoding = encoder

    def read_file_content(self, path):
        return "dummy"

    def count_tokens(self, text):
        return len(self._encoding.encode(text, disallowed_special=()))

class DummyClient:
    class responses:
        @staticmethod
        def create(**kwargs):
            return types.SimpleNamespace(
                output_text="summary"
            )

class DummyLimiter:
    rpm = 1000
    tpm = 250_000
    context_window = 131_072

    def acquire(self, _tokens):
        return None


def _patch_groq(monkeypatch):
    monkeypatch.setattr(summary_service, "groq_available", lambda: True)
    monkeypatch.setattr(summary_service, "get_groq_client", lambda: DummyClient())
    monkeypatch.setattr(summary_service, "get_rate_limiter", lambda *_args, **_kwargs: DummyLimiter())

class DummyExecutor:
    def __init__(self, mw):
        self.max_workers = mw
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def submit(self, fn, *a, **k):
        fut = concurrent.futures.Future()
        fut.set_result(None)
        return fut


def test_workers_from_config(monkeypatch):
    recorded = []
    _patch_groq(monkeypatch)
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", lambda max_workers: (recorded.append(max_workers), DummyExecutor(max_workers))[1])
    cfg = {"groq_max_workers": 3}
    summarizer.fetch_ai_summaries(["a"], DummyFP(), "short_only", app_config=cfg)
    assert recorded[0] == 3


def test_workers_auto(monkeypatch):
    recorded = []
    _patch_groq(monkeypatch)
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", lambda max_workers: (recorded.append(max_workers), DummyExecutor(max_workers))[1])
    cfg = {"groq_max_workers": 0}
    files = ["a", "b"]
    cpu = os.cpu_count() or 4
    expected = min(len(files), max(4, cpu * 2, 34), 64)
    summarizer.fetch_ai_summaries(files, DummyFP(), "short_only", app_config=cfg)
    assert recorded[0] == max(1, expected)


def test_groq_workers_auto_capped(monkeypatch):
    recorded = []

    monkeypatch.setattr(summary_service, "groq_available", lambda: True)

    class DummyGroqClient:
        class responses:
            @staticmethod
            def create(**_kwargs):
                return types.SimpleNamespace(
                    output_text="summary"
                )

    class DummyLimiter:
        rpm = 9999
        tpm = 1_000_000
        context_window = 131_072

        def acquire(self, _tokens):
            return None

    monkeypatch.setattr(summary_service, "get_groq_client", lambda: DummyGroqClient())
    monkeypatch.setattr(summary_service, "get_rate_limiter", lambda *_args, **_kwargs: DummyLimiter())
    monkeypatch.setattr(
        concurrent.futures,
        "ThreadPoolExecutor",
        lambda max_workers: (recorded.append(max_workers), DummyExecutor(max_workers))[1],
    )

    files = [f"f{i}.py" for i in range(100)]
    summarizer.fetch_ai_summaries(files, DummyFP(), "short_only", app_config={})

    expected = min(len(files), 64)
    assert recorded[0] == max(1, expected)
