from __future__ import annotations

from typing import Any

from .logger import get_logger, log_counter, log_event


# Canonical metric event names used across the application.
EVENT = {
    "MERGE_PLAN_START": "merge_plan_start",
    "MERGE_PLAN_END": "merge_plan_end",
    "WATCH_TICK": "watch_tick",
    "SUMMARY_ORCHESTRATE_START": "summary_orchestrate_start",
    "SUMMARY_ORCHESTRATE_END": "summary_orchestrate_end",
    "SUMMARY_CACHE_HIT": "summary_cache_hit",
    "SUMMARY_CACHE_MISS": "summary_cache_miss",
    "SUMMARY_CACHE_STALE": "summary_cache_stale",
    "SUMMARY_PUT_OK": "summary_put_ok",
    "SUMMARY_PUT_ERROR": "summary_put_error",
}


_metrics_logger = get_logger("metrics")


def emit(name: str, **fields: Any) -> None:
    """Emit a structured metrics event."""

    log_event(_metrics_logger, name, **fields)


def count(name: str, value: int = 1, **fields: Any) -> None:
    """Emit a counter-style metrics event."""

    log_counter(_metrics_logger, name, value, **fields)
