from __future__ import annotations

from typing import Callable, Sequence


def format_item(label: str, text: str) -> str:
    label_text = str(label or "").strip()
    body = str(text or "").strip()
    header = f"[{label_text}]" if label_text else "[item]"
    if body:
        return f"{header}\n{body}"
    return header


def format_items(items: Sequence[tuple[str, str]]) -> str:
    parts = [format_item(label, text) for label, text in items]
    return "\n\n".join(parts).strip()


def _safe_count(token_counter: Callable[[str], int], text: str) -> int:
    try:
        return max(int(token_counter(text)), 1)
    except Exception:
        return max(len(text), 1)


def pack_items_by_token_budget(
    items: Sequence[tuple[str, str]],
    *,
    token_counter: Callable[[str], int],
    budget_tokens: int = 80_000,
    overhead_reserve_tokens: int = 2_000,
) -> list[list[tuple[str, str]]]:
    budget = max(1, int(budget_tokens) - int(overhead_reserve_tokens))
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_tokens = 0
    separator_tokens = _safe_count(token_counter, "\n\n")

    for label, text in items:
        formatted = format_item(label, text)
        item_tokens = _safe_count(token_counter, formatted)
        extra_tokens = item_tokens + (separator_tokens if current else 0)
        if current and (current_tokens + extra_tokens) > budget:
            groups.append(current)
            current = []
            current_tokens = 0
            extra_tokens = item_tokens
        current.append((label, text))
        current_tokens += extra_tokens

    if current:
        groups.append(current)
    return groups


def _call_summarize(
    summarize_fn: Callable[..., str],
    prompt_text: str,
    *,
    log_context: dict | None,
) -> str:
    try:
        return summarize_fn(prompt_text, log_context=log_context)
    except TypeError:
        return summarize_fn(prompt_text)


def reduce_items_to_one_summary(
    items: Sequence[tuple[str, str]],
    *,
    prompt_builder: Callable[[Sequence[tuple[str, str]]], str],
    summarize_fn: Callable[..., str],
    token_counter: Callable[[str], int],
    budget_tokens: int = 80_000,
    overhead_reserve_tokens: int = 2_000,
    label_prefix: str = "GROUP",
    always_summarize: bool = False,
) -> tuple[str, int, list[int]]:
    working = list(items)
    if not working:
        return "", 0, []
    if len(working) == 1 and not always_summarize:
        return working[0][1], 0, []
    if len(working) == 1 and always_summarize:
        prompt_text = prompt_builder(working)
        summary_text = _call_summarize(
            summarize_fn,
            prompt_text,
            log_context={"layer": 1, "group_index": 1, "group_total": 1},
        )
        return summary_text, 1, [1]

    reduction_layers = 0
    group_counts: list[int] = []

    while len(working) > 1:
        groups = pack_items_by_token_budget(
            working,
            token_counter=token_counter,
            budget_tokens=budget_tokens,
            overhead_reserve_tokens=overhead_reserve_tokens,
        )
        reduction_layers += 1
        group_counts.append(len(groups))
        next_items: list[tuple[str, str]] = []
        for idx, group in enumerate(groups, start=1):
            prompt_text = prompt_builder(group)
            log_context = {
                "layer": reduction_layers,
                "group_index": idx,
                "group_total": len(groups),
            }
            summary_text = _call_summarize(
                summarize_fn,
                prompt_text,
                log_context=log_context,
            )
            label = f"{label_prefix} L{reduction_layers} #{idx}"
            next_items.append((label, summary_text))
        working = next_items

    return working[0][1], reduction_layers, group_counts


__all__ = [
    "format_item",
    "format_items",
    "pack_items_by_token_budget",
    "reduce_items_to_one_summary",
]
