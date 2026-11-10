from __future__ import annotations

from typing import Dict, Optional

from .config import (
    DEFAULT_GROQ_SUMMARY_MODEL,
    SUMMARY_PROMPT_OVERHEAD,
    DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS,
    DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS,
    DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS,
    DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS,
    DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS,
)
from .rate_limiter import get_context_window, get_max_output_tokens

SUMMARY_MODEL = DEFAULT_GROQ_SUMMARY_MODEL
DEFAULT_CONTEXT_WINDOW = 131_072
DEFAULT_MAX_OUTPUT_TOKENS = 65_536


def resolve_summary_model(app_config: Optional[Dict]) -> str:
    raw = app_config.get("groq_summary_model") if isinstance(app_config, dict) else None
    if raw:
        return str(raw)
    return SUMMARY_MODEL


def resolve_verifier_model(app_config: Optional[Dict]) -> str:
    cfg = app_config or {}
    raw = cfg.get("groq_verifier_model") or cfg.get("groq_summary_model")
    if raw:
        return str(raw)
    return SUMMARY_MODEL


def resolve_full_summary_model(app_config: Optional[Dict]) -> str:
    cfg = app_config or {}
    raw = cfg.get("full_summary_model") or cfg.get("groq_summary_model")
    if raw:
        return str(raw)
    return SUMMARY_MODEL


def summary_model_caps(model_name: str | None = None) -> tuple[int, int]:
    name = model_name or SUMMARY_MODEL
    context_window = get_context_window(name) or DEFAULT_CONTEXT_WINDOW
    max_output = get_max_output_tokens(name)
    if max_output <= 0:
        max_output = DEFAULT_MAX_OUTPUT_TOKENS
    return int(context_window), int(max_output)


def get_summary_model_caps(model_name: str | None = None) -> tuple[int, int]:
    """Public helper to retrieve model context and output limits."""
    return summary_model_caps(model_name)


def resolve_max_output_tokens(
    app_config: Optional[Dict],
    call_kind: str,
    *,
    model_name: str | None = None,
) -> int:
    cfg = app_config or {}
    defaults = {
        "file": DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS,
        "unifier": DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS,
        "aggregate": DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS,
        "selector": DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS,
        "critic": DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS,
    }
    key_map = {
        "file": "file_summary_max_output_tokens",
        "unifier": "file_summary_unifier_max_output_tokens",
        "aggregate": "aggregate_summary_max_output_tokens",
        "selector": "aggregate_selector_max_output_tokens",
        "critic": "aggregate_critic_max_output_tokens",
    }
    key = key_map.get(call_kind, "")
    raw = cfg.get(key, defaults.get(call_kind, 0)) if key else defaults.get(call_kind, 0)
    try:
        value = int(raw or 0)
    except Exception:
        value = 0
    if value <= 0:
        value = int(defaults.get(call_kind, 0) or 0)
    if call_kind in {"file", "unifier", "aggregate", "selector", "critic"} and value < 15_000:
        value = 15_000
    model_cap = get_max_output_tokens(model_name or SUMMARY_MODEL)
    if model_cap and model_cap > 0:
        value = min(value, int(model_cap))
    if value <= 0:
        value = int(DEFAULT_MAX_OUTPUT_TOKENS)
    return int(value)


def resolve_prompt_budget(
    app_config: dict,
    *,
    model_name: str,
    call_kind: str = "aggregate",
) -> int:
    context_window, _ = summary_model_caps(model_name)
    max_output_tokens = resolve_max_output_tokens(app_config, call_kind, model_name=model_name)
    max_prompt = max(1, context_window - max_output_tokens - SUMMARY_PROMPT_OVERHEAD)
    raw = app_config.get("aggregate_prompt_budget_tokens", 0)
    try:
        budget = int(raw or 0)
    except Exception:
        budget = 0
    if budget <= 0:
        budget = max_prompt
    return min(budget, max_prompt)
