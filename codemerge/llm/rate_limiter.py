"""Simple rate limiter for Groq-hosted LLM requests.

IMPORTANT: This rate limiter is process-local and thread-safe, but does NOT
coordinate across multiple processes. If running with multiple workers (e.g.,
uvicorn with --workers > 1 or gunicorn), each worker will have its own limiter
instance and the combined request rate may exceed provider limits.

For production multi-process deployments, consider:
- Running with a single worker (recommended during development)
- Implementing a shared cross-process limiter using Redis or a file-lock
  based token bucket stored in a SQLite database.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque


class RateLimiter:
    """Token and request based rate limiter for Groq's OpenAI-compatible API."""

    def __init__(self, rpm: int, tpm: int, *, context_window: int | None = None) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self.context_window = context_window
        self._req_times: deque[float] = deque()
        self._token_records: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def acquire(self, tokens: int) -> None:
        """Block until a new request is allowed."""
        if tokens > self.tpm:
            raise ValueError("The request exceeds the local per-minute token budget.")
        while True:
            with self._lock:
                now = time.monotonic()
                # purge old records
                while self._req_times and now - self._req_times[0] >= 60:
                    self._req_times.popleft()
                while self._token_records and now - self._token_records[0][0] >= 60:
                    self._token_records.popleft()

                req_count = len(self._req_times)
                token_sum = sum(t for _, t in self._token_records)

                if req_count < self.rpm and token_sum + tokens <= self.tpm:
                    self._req_times.append(now)
                    self._token_records.append((now, tokens))
                    return

                wait_times = []
                if req_count >= self.rpm:
                    wait_times.append(60 - (now - self._req_times[0]))
                if token_sum + tokens > self.tpm:
                    wait_times.append(60 - (now - self._token_records[0][0]))
                wait = max(wait_times) if wait_times else 0
            time.sleep(max(wait, 0.05))


# Rate limits sourced from Groq's 2025 production quotas (requests per minute &
# tokens per minute). Tokens are measured on the prompt side. We do not enforce
# the provider's generous daily limits here, only per-minute gating. "Short"
# and "long" prompts share the same limits but the structure keeps backwards
# compatibility with earlier callers that keyed by mode.
MODEL_LIMITS = {
    "llama-3.1-8b-instant": {
        "short": {"rpm": 1_000, "tpm": 250_000},
        "long": {"rpm": 1_000, "tpm": 250_000},
        "context_window": 131_072,
    },
    "llama-3.3-70b-versatile": {
        "short": {"rpm": 1_000, "tpm": 300_000},
        "long": {"rpm": 1_000, "tpm": 300_000},
        "context_window": 131_072,
    },
    "openai/gpt-oss-20b": {
        "short": {"rpm": 1_000, "tpm": 250_000},
        "long": {"rpm": 1_000, "tpm": 250_000},
        "context_window": 131_072,
    },
    "openai/gpt-oss-120b": {
        "short": {"rpm": 1_000, "tpm": 250_000},
        "long": {"rpm": 1_000, "tpm": 250_000},
        "context_window": 131_072,
    },
    "qwen/qwen3-32b": {
        "short": {"rpm": 1_000, "tpm": 300_000},
        "long": {"rpm": 1_000, "tpm": 300_000},
        "context_window": 131_072,
    },
    "meta-llama/llama-guard-4-12b": {
        "short": {"rpm": 100, "tpm": 30_000},
        "long": {"rpm": 100, "tpm": 30_000},
        "context_window": 131_072,
    },
    "meta-llama/llama-4-maverick-17b-12b-instruct": {
        "short": {"rpm": 1_000, "tpm": 300_000},
        "long": {"rpm": 1_000, "tpm": 300_000},
        "context_window": 131_072,
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "short": {"rpm": 1_000, "tpm": 300_000},
        "long": {"rpm": 1_000, "tpm": 300_000},
        "context_window": 131_072,
    },
    "groq/compound": {
        "short": {"rpm": 200, "tpm": 200_000},
        "long": {"rpm": 200, "tpm": 200_000},
        "context_window": 131_072,
    },
    "groq/compound-mini": {
        "short": {"rpm": 200, "tpm": 200_000},
        "long": {"rpm": 200, "tpm": 200_000},
        "context_window": 131_072,
    },
    "gemma2-9b-it": {
        "short": {"rpm": 200, "tpm": 30_000},
        "long": {"rpm": 200, "tpm": 30_000},
        "context_window": 131_072,
    },
    "deepseek-r1-distill-llama-70b": {
        "short": {"rpm": 1_000, "tpm": 300_000},
        "long": {"rpm": 1_000, "tpm": 300_000},
        "context_window": 131_072,
    },
    "allam-2-7b": {
        "short": {"rpm": 300, "tpm": 60_000},
        "long": {"rpm": 300, "tpm": 60_000},
        "context_window": 131_072,
    },
}

DEFAULT_MODEL = "llama-3.1-8b-instant"
DEFAULT_MAX_OUTPUT_TOKENS = 65_536
MODEL_MAX_OUTPUT_TOKENS = {
    "llama-3.1-8b-instant": DEFAULT_MAX_OUTPUT_TOKENS,
    "llama-3.3-70b-versatile": DEFAULT_MAX_OUTPUT_TOKENS,
    "openai/gpt-oss-20b": DEFAULT_MAX_OUTPUT_TOKENS,
    "openai/gpt-oss-120b": DEFAULT_MAX_OUTPUT_TOKENS,
    "qwen/qwen3-32b": DEFAULT_MAX_OUTPUT_TOKENS,
    "meta-llama/llama-guard-4-12b": DEFAULT_MAX_OUTPUT_TOKENS,
    "meta-llama/llama-4-maverick-17b-12b-instruct": DEFAULT_MAX_OUTPUT_TOKENS,
    "meta-llama/llama-4-scout-17b-16e-instruct": DEFAULT_MAX_OUTPUT_TOKENS,
    "groq/compound": DEFAULT_MAX_OUTPUT_TOKENS,
    "groq/compound-mini": DEFAULT_MAX_OUTPUT_TOKENS,
    "gemma2-9b-it": DEFAULT_MAX_OUTPUT_TOKENS,
    "deepseek-r1-distill-llama-70b": DEFAULT_MAX_OUTPUT_TOKENS,
    "allam-2-7b": DEFAULT_MAX_OUTPUT_TOKENS,
}

TOKEN_SAFETY_MULTIPLIER = 1.12
TOKEN_SAFETY_BUFFER = 128

_LIMITER_LOCK = threading.Lock()
_LIMITER_CACHE: dict[str, "RateLimiter"] = {}


def get_rate_limiter(model_name: str, content_mode: str) -> RateLimiter:
    """Return a :class:`RateLimiter` configured for ``model_name``."""
    key_lookup = (model_name or DEFAULT_MODEL).lower()
    limits = MODEL_LIMITS.get(key_lookup, MODEL_LIMITS[DEFAULT_MODEL])
    short = limits.get("short") or {}
    long = limits.get("long") or {}
    rpm = min(int(short.get("rpm", 1_000)), int(long.get("rpm", 1_000)))
    tpm = min(int(short.get("tpm", 250_000)), int(long.get("tpm", 250_000)))
    with _LIMITER_LOCK:
        limiter = _LIMITER_CACHE.get(key_lookup)
        if limiter is None:
            limiter = RateLimiter(rpm, tpm, context_window=limits.get("context_window"))
            _LIMITER_CACHE[key_lookup] = limiter
    return limiter


def reserve_tokens(prompt_tokens: int) -> int:
    """Apply a conservative safety margin for provider token accounting."""
    base = max(int(prompt_tokens), 1)
    return int(math.ceil(base * TOKEN_SAFETY_MULTIPLIER)) + TOKEN_SAFETY_BUFFER


def get_context_window(model_name: str | None) -> int | None:
    """Return the configured context window for ``model_name`` if known."""

    if not model_name:
        model_name = DEFAULT_MODEL
    limits = MODEL_LIMITS.get(model_name.lower())
    if not limits:
        return None
    return limits.get("context_window")


def get_max_output_tokens(model_name: str | None) -> int:
    """Return the configured max output tokens for ``model_name`` if known."""

    if not model_name:
        model_name = DEFAULT_MODEL
    key = model_name.lower()
    return int(MODEL_MAX_OUTPUT_TOKENS.get(key, DEFAULT_MAX_OUTPUT_TOKENS))
