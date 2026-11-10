"""LLM-backed pruning and agent operations."""

from __future__ import annotations

from ._prune_service import (
    drop_spans_with_llm,
    interpret_goal_with_llm,
    run_pruning_agent_exchange,
    select_spans_with_llm,
    suggest_stubs,
    synthesize_harness_cases,
)
from ._prune_tools import DELETE_DEFS_TOOL, SUGGEST_STUBS_TOOL

__all__ = [
    "DELETE_DEFS_TOOL",
    "SUGGEST_STUBS_TOOL",
    "interpret_goal_with_llm",
    "select_spans_with_llm",
    "drop_spans_with_llm",
    "suggest_stubs",
    "synthesize_harness_cases",
    "run_pruning_agent_exchange",
]
