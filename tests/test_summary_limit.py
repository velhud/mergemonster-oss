import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from codemerge.core.tokens import get_encoding

from codemerge.llm import summarize as summarizer
from codemerge.llm import _summarize_service as summary_service
from codemerge.llm.config import default_summary_token_limit

encoder = get_encoding()


class DummyEnc:
    def __init__(self):
        self.called = False

    def encode(self, text, disallowed_special=()):
        self.called = True
        return encoder.encode(text, disallowed_special=disallowed_special)

    def decode(self, tokens):
        return encoder.decode(tokens)


class DummyFP:
    def __init__(self, enc):
        self._encoding = enc

    def read_file_content(self, path):
        return " ".join(str(i) for i in range(120))

    def count_tokens(self, text):
        return len(self._encoding.encode(text, disallowed_special=()))


class DummyClient:
    captured = []

    class responses:
        @staticmethod
        def create(**kwargs):
            DummyClient.captured.append(kwargs["input"][1]["content"])
            return types.SimpleNamespace(
                output_text="summary"
            )


class DummyLimiter:
    rpm = 1000
    tpm = 1_000_000
    context_window = 131_072

    def acquire(self, _tokens):
        return None


def _patch_groq(monkeypatch):
    monkeypatch.setattr(summary_service, "groq_available", lambda: True)
    monkeypatch.setattr(summary_service, "get_groq_client", lambda: DummyClient())
    monkeypatch.setattr(summary_service, "get_rate_limiter", lambda *_args, **_kwargs: DummyLimiter())


def test_chunking_sends_all_content(monkeypatch):
    enc = DummyEnc()
    DummyClient.captured = []
    _patch_groq(monkeypatch)
    summarizer.fetch_ai_summaries(
        ["a"],
        DummyFP(enc),
        "short_only",
        app_config={},
    )
    captured = "\n".join(DummyClient.captured)
    assert len(DummyClient.captured) == 1
    assert "0 1" in captured
    assert "59" in captured
    assert "119" in captured


def test_default_limit_by_model():
    assert default_summary_token_limit("openai/gpt-oss-20b") >= 20_000
