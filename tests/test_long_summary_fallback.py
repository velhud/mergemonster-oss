import types
from codemerge.core.tokens import get_encoding
from codemerge.llm import summarize as summarizer
from codemerge.llm import _summarize_service as summary_service

encoder = get_encoding()


class DummyFP:
    _encoding = encoder

    def read_file_content(self, path):
        return "x " * 50

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


def test_small_file_uses_plain_chunk_prompt(monkeypatch):
    DummyClient.captured = []
    _patch_groq(monkeypatch)
    summarizer.fetch_ai_summaries(["a"], DummyFP(), "long_only", app_config={})
    joined = "\n".join(DummyClient.captured)
    assert "Job: FILE_SUMMARY_LONG_FROM_CONTENT" in joined
    assert "## Uncertainties" in joined
    assert "x x" in joined
