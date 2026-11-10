from __future__ import annotations

import logging
from typing import Callable, Dict

from .embed_types import (
    DEFAULT_GROQ_EMBED_MODEL,
    DEFAULT_OPENAI_EMBED_MODEL,
    max_dimensions_for_model,
    supports_custom_dimensions,
)
from .rate_limiter import get_context_window

# Default Groq chat models. All responses request up to 20k tokens to minimize
# truncation unless the configuration overrides the limit.
DEFAULT_GROQ_SUMMARY_MODEL = "openai/gpt-oss-20b"
DEFAULT_GROQ_DECIDER_MODEL = "openai/gpt-oss-20b"
DEFAULT_GROQ_VERIFIER_MODEL = "openai/gpt-oss-20b"
DEFAULT_GROQ_MODEL = DEFAULT_GROQ_DECIDER_MODEL

# Legacy aliases retained for modules migrated in earlier releases.
DEFAULT_OPENAI_SUMMARY_MODEL = DEFAULT_GROQ_SUMMARY_MODEL
DEFAULT_OPENAI_DECIDER_MODEL = DEFAULT_GROQ_DECIDER_MODEL
DEFAULT_OPENAI_VERIFIER_MODEL = DEFAULT_GROQ_VERIFIER_MODEL
DEFAULT_OPENAI_MODEL = DEFAULT_GROQ_MODEL
# Keep the historical OpenAI embedding default for config migrations.

SUMMARY_PROMPT_OVERHEAD = 1_024
DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS = 15_000
DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS = 15_000
DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS = 25_000
DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS = 15_000
DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS = 15_000


def default_file_summary_chunk_tokens(model: str | None) -> int:
    """Return the default chunk size for file summarization."""
    _ = model
    return 20_000


def default_aggregate_prompt_budget(model: str | None) -> int:
    """Return the default prompt budget for aggregate summaries."""
    if not model:
        return 0
    name = model.lower()
    context_tokens = get_context_window(name)
    if context_tokens:
        headroom = DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS + SUMMARY_PROMPT_OVERHEAD
        return max(0, context_tokens - headroom)
    return 0


def default_summary_token_limit(model: str | None) -> int:
    """Backward-compatible alias for file summary chunk sizing."""
    return default_file_summary_chunk_tokens(model)


def enforce_output_token_minimums(cfg: Dict) -> None:
    floors = {
        "aggregate_selector_max_output_tokens": 15_000,
        "aggregate_summary_max_output_tokens": 25_000,
        "aggregate_critic_max_output_tokens": 15_000,
        "file_summary_max_output_tokens": 15_000,
        "file_summary_unifier_max_output_tokens": 15_000,
    }
    for key, floor in floors.items():
        try:
            val = int(cfg.get(key, 0) or 0)
        except Exception:
            val = 0
        if val < floor:
            cfg[key] = floor


def normalize_groq_models(cfg: Dict, *, save_callback: Callable[[], None] | None = None) -> None:
    """Ensure Groq model selections are valid after configuration load."""

    embedding_provider = str(cfg.get("embedding_provider", "groq") or "").lower()
    replacements: Dict[str, str] = {}

    def _needs_reset(value: str | None) -> bool:
        if value is None:
            return True
        value_str = str(value).strip().lower()
        if not value_str:
            return True
        # Groq endpoint does not host OpenAI "gpt-*" or related identifiers.
        return value_str.startswith("gpt-") or value_str.startswith("gpt_")

    if _needs_reset(cfg.get("groq_model")):
        replacements["groq_model"] = DEFAULT_GROQ_MODEL
    if _needs_reset(cfg.get("groq_decider_model")):
        replacements["groq_decider_model"] = DEFAULT_GROQ_DECIDER_MODEL
    if _needs_reset(cfg.get("groq_verifier_model")):
        replacements["groq_verifier_model"] = DEFAULT_GROQ_VERIFIER_MODEL

    summary_model = cfg.get("groq_summary_model")
    if _needs_reset(summary_model):
        replacements["groq_summary_model"] = DEFAULT_GROQ_SUMMARY_MODEL

    embed_value = cfg.get("groq_embedding_model")
    if embedding_provider == "groq" and (
        _needs_reset(embed_value)
        or str(embed_value).strip().lower() == "text-embedding-3-large"
    ):
        replacements["groq_embedding_model"] = DEFAULT_GROQ_EMBED_MODEL

    if not replacements:
        return

    logging.info(
        "Resetting Groq model selections to defaults: %s",
        ", ".join(f"{k}->{v}" for k, v in replacements.items()),
    )
    cfg.update(replacements)
    if save_callback is not None:
        try:
            save_callback()
        except Exception as exc:
            logging.warning("Failed to persist Groq model normalization: %s", exc)


def normalize_openai_embeddings(cfg: Dict) -> None:
    """Clamp OpenAI embedding settings to supported defaults."""

    model_raw = cfg.get("openai_embedding_model")
    model = str(model_raw).strip() if model_raw else ""
    if not model:
        model = DEFAULT_OPENAI_EMBED_MODEL
        cfg["openai_embedding_model"] = model

    dims_raw = cfg.get("openai_embedding_dimensions", 0)
    try:
        dims = int(dims_raw or 0)
    except Exception:
        dims = 0
    if dims < 0:
        dims = 0

    if not supports_custom_dimensions(model):
        if dims > 0:
            logging.info(
                "Clearing OpenAI embedding dimensions for model without custom dimension support: %s",
                model,
            )
        dims = 0
    else:
        limit = max_dimensions_for_model(model)
        if limit is not None and dims > limit:
            logging.info(
                "Clamping OpenAI embedding dimensions %s->%s for model %s",
                dims,
                limit,
                model,
            )
            dims = limit

    cfg["openai_embedding_dimensions"] = dims


__all__ = [
    "DEFAULT_GROQ_SUMMARY_MODEL",
    "DEFAULT_GROQ_DECIDER_MODEL",
    "DEFAULT_GROQ_VERIFIER_MODEL",
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_OPENAI_SUMMARY_MODEL",
    "DEFAULT_OPENAI_DECIDER_MODEL",
    "DEFAULT_OPENAI_VERIFIER_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "SUMMARY_PROMPT_OVERHEAD",
    "DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS",
    "DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS",
    "DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS",
    "DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS",
    "DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS",
    "default_file_summary_chunk_tokens",
    "default_aggregate_prompt_budget",
    "default_summary_token_limit",
    "enforce_output_token_minimums",
    "normalize_groq_models",
    "normalize_openai_embeddings",
]
