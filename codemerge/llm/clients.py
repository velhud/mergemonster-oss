"""LLM client configuration and factories."""

from __future__ import annotations

import os
from functools import lru_cache

from ..logger import get_logger

logger = get_logger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_KEY_ENV = "GROQ_API_KEY"
OPENAI_KEY_ENV = "OPENAI_API_KEY"

try:  # pragma: no cover - optional dependency guard
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore


def groq_available() -> bool:
    """Return True when the runtime can create Groq clients."""
    if OpenAI is None:
        return False
    key = os.getenv(GROQ_KEY_ENV)
    if not key:
        logger.warning("GROQ_API_KEY not configured; Groq models unavailable.")
        return False
    return True


@lru_cache(maxsize=1)
def get_groq_client() -> object:
    """Return a configured OpenAI client targeting Groq's endpoint."""
    if OpenAI is None:
        raise RuntimeError("Install the openai Python package to use Groq models.")
    key = os.getenv(GROQ_KEY_ENV)
    if not key:
        raise RuntimeError("Set GROQ_API_KEY to enable Groq requests.")
    return OpenAI(api_key=key, base_url=GROQ_BASE_URL)

def supports_batch_api() -> bool:
    """Groq does not expose the OpenAI Batch API today."""
    return False


def groq_endpoint() -> str:
    """Expose the Groq base URL for callers that need it."""
    return GROQ_BASE_URL


def openai_available() -> bool:
    """Return True when the runtime can create OpenAI clients."""
    if OpenAI is None:
        return False
    key = os.getenv(OPENAI_KEY_ENV)
    if not key:
        logger.warning("OPENAI_API_KEY not configured; OpenAI models unavailable.")
        return False
    return True


@lru_cache(maxsize=1)
def get_openai_client() -> object:
    """Return a configured OpenAI client."""
    if OpenAI is None:
        raise RuntimeError("Install the openai Python package to use OpenAI models.")
    key = os.getenv(OPENAI_KEY_ENV)
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY to use OpenAI models.")
    return OpenAI(api_key=key)
