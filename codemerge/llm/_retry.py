from __future__ import annotations

import re
from typing import Optional

_TRANSIENT_STATUS_CODES = {408, 409, 423, 425, 429, 500, 502, 503, 504, 524}
_TRANSIENT_PROBES = (
    "rate limit",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "service unavailable",
    "connection reset",
    "connection aborted",
    "connection refused",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "overloaded",
    "tool_use_failed",
    "tool choice is none, but model called a tool",
)


def extract_retry_after_seconds(error: Exception) -> Optional[float]:
    msg = str(error)
    match = re.search(r"try again in\s*([0-9]+(?:\.[0-9]+)?)s", msg, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except Exception:
            return None
    return None


def is_rate_limit_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status == 429:
        return True
    msg = str(error)
    return "429" in msg or "rate limit" in msg.lower()


def is_retryable_error(error: Exception) -> bool:
    if is_rate_limit_error(error):
        return True
    status = getattr(error, "status_code", None)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    msg = str(error).lower()
    return any(probe in msg for probe in _TRANSIENT_PROBES)
