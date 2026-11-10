from __future__ import annotations

from typing import Callable


def _tiktoken_count(text: str) -> int | None:
    try:
        from ..core.tokens import count_tokens

        return count_tokens(text)
    except Exception:
        return None


def estimate_tokens(text: str, *, fallback_scale: int = 4) -> int:
    fallback_scale = max(1, int(fallback_scale))
    tcount = _tiktoken_count(text)
    if tcount is not None:
        return max(int(tcount), 1)
    return max(len(text) // fallback_scale, 1)


def safe_count_tokens(
    text: str,
    token_counter: Callable[[str], int] | None = None,
    *,
    fallback_scale: int = 4,
    use_tiktoken: bool = True,
) -> int:
    if token_counter is not None:
        try:
            return max(int(token_counter(text)), 1)
        except Exception:
            pass
    if use_tiktoken:
        tcount = _tiktoken_count(text)
        if tcount is not None:
            return max(int(tcount), 1)
    return max(len(text) // max(1, int(fallback_scale)), 1)
