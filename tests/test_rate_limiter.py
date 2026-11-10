from __future__ import annotations

import math

from codemerge.llm import rate_limiter as rl


def test_rate_limiter_respects_token_budget(monkeypatch):
    limiter = rl.RateLimiter(rpm=10, tpm=5)

    current = {"value": 0.0}

    def fake_monotonic() -> float:
        return current["value"]

    def fake_sleep(duration: float) -> None:
        current["value"] += duration

    monkeypatch.setattr(rl.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(rl.time, "sleep", fake_sleep)

    limiter.acquire(3)
    assert current["value"] == 0

    limiter.acquire(3)
    assert current["value"] >= 60


def test_rate_limiter_respects_request_budget(monkeypatch):
    limiter = rl.RateLimiter(rpm=2, tpm=100)

    current = {"value": 0.0}

    def fake_monotonic() -> float:
        return current["value"]

    def fake_sleep(duration: float) -> None:
        current["value"] += duration

    monkeypatch.setattr(rl.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(rl.time, "sleep", fake_sleep)

    limiter.acquire(1)
    limiter.acquire(1)
    limiter.acquire(1)
    assert current["value"] >= 60


def test_model_limits_constants_locked():
    expected = {
        "allam-2-7b": {
            "context_window": 131_072,
            "short": {"rpm": 300, "tpm": 60_000},
            "long": {"rpm": 300, "tpm": 60_000},
        },
        "deepseek-r1-distill-llama-70b": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 300_000},
            "long": {"rpm": 1_000, "tpm": 300_000},
        },
        "gemma2-9b-it": {
            "context_window": 131_072,
            "short": {"rpm": 200, "tpm": 30_000},
            "long": {"rpm": 200, "tpm": 30_000},
        },
        "groq/compound": {
            "context_window": 131_072,
            "short": {"rpm": 200, "tpm": 200_000},
            "long": {"rpm": 200, "tpm": 200_000},
        },
        "groq/compound-mini": {
            "context_window": 131_072,
            "short": {"rpm": 200, "tpm": 200_000},
            "long": {"rpm": 200, "tpm": 200_000},
        },
        "llama-3.1-8b-instant": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 250_000},
            "long": {"rpm": 1_000, "tpm": 250_000},
        },
        "llama-3.3-70b-versatile": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 300_000},
            "long": {"rpm": 1_000, "tpm": 300_000},
        },
        "meta-llama/llama-4-maverick-17b-12b-instruct": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 300_000},
            "long": {"rpm": 1_000, "tpm": 300_000},
        },
        "meta-llama/llama-4-scout-17b-16e-instruct": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 300_000},
            "long": {"rpm": 1_000, "tpm": 300_000},
        },
        "meta-llama/llama-guard-4-12b": {
            "context_window": 131_072,
            "short": {"rpm": 100, "tpm": 30_000},
            "long": {"rpm": 100, "tpm": 30_000},
        },
        "openai/gpt-oss-120b": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 250_000},
            "long": {"rpm": 1_000, "tpm": 250_000},
        },
        "openai/gpt-oss-20b": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 250_000},
            "long": {"rpm": 1_000, "tpm": 250_000},
        },
        "qwen/qwen3-32b": {
            "context_window": 131_072,
            "short": {"rpm": 1_000, "tpm": 300_000},
            "long": {"rpm": 1_000, "tpm": 300_000},
        },
    }
    assert rl.MODEL_LIMITS == expected


def test_rate_limiter_singleton_per_model():
    limiter_a = rl.get_rate_limiter("openai/gpt-oss-20b", "short_only")
    limiter_b = rl.get_rate_limiter("openai/gpt-oss-20b", "long_only")
    limiter_c = rl.get_rate_limiter("openai/gpt-oss-120b", "short_only")
    assert limiter_a is limiter_b
    assert limiter_a is not limiter_c


def test_reserve_tokens_applies_safety_margin():
    base = 1_000
    expected = int(math.ceil(base * rl.TOKEN_SAFETY_MULTIPLIER)) + rl.TOKEN_SAFETY_BUFFER
    assert rl.reserve_tokens(base) == expected


def test_rate_limiter_rejects_single_request_larger_than_budget():
    import pytest

    limiter = rl.RateLimiter(rpm=10, tpm=5)
    with pytest.raises(ValueError, match="local per-minute token budget"):
        limiter.acquire(6)
