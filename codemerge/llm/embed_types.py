from __future__ import annotations

from enum import Enum

# Groq does not currently expose an embeddings endpoint. We keep an explicit
# empty default so callers can detect missing values and fall back to OpenAI.
DEFAULT_GROQ_EMBED_MODEL = ""

# Latest OpenAI defaults aligned with the 2024 third-generation embeddings.
DEFAULT_OPENAI_EMBED_MODEL = "text-embedding-3-large"
DEFAULT_OPENAI_EMBED_DIMENSIONS = 3_072
DEFAULT_OPENAI_SMALL_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_SMALL_DIMENSIONS = 1_536


class EmbedProvider(Enum):
    NONE = "none"
    GROQ = "groq"
    OPENAI = "openai"


def max_dimensions_for_model(model: str | None) -> int | None:
    """Return the maximum supported dimensionality for a given OpenAI model."""

    if not model:
        return None
    name = model.strip().lower()
    if name == DEFAULT_OPENAI_EMBED_MODEL:
        return DEFAULT_OPENAI_EMBED_DIMENSIONS
    if name == DEFAULT_OPENAI_SMALL_MODEL:
        return DEFAULT_OPENAI_SMALL_DIMENSIONS
    if name.endswith("-3-large"):
        return DEFAULT_OPENAI_EMBED_DIMENSIONS
    if name.endswith("-3-small"):
        return DEFAULT_OPENAI_SMALL_DIMENSIONS
    return None


def supports_custom_dimensions(model: str | None) -> bool:
    """Return ``True`` when the OpenAI model accepts the ``dimensions`` flag."""

    if not model:
        return False
    name = model.strip().lower()
    return name.startswith("text-embedding-3-")
