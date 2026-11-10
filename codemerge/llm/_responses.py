from __future__ import annotations

import json
from typing import Optional


def _extract_output_chunks(output: object) -> list[str]:
    chunks: list[str] = []
    items = output if isinstance(output, list) else [output]
    for item in items:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if content is None:
            continue
        parts = content if isinstance(content, list) else [content]
        for part in parts:
            piece = getattr(part, "text", None)
            if piece is None and isinstance(part, dict):
                piece = part.get("text")
            if isinstance(piece, str):
                chunks.append(piece)
    return chunks


def extract_text(response: object) -> Optional[str]:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    output = getattr(response, "output", None)
    if output is None:
        return text if isinstance(text, str) and text else None
    try:
        chunks = _extract_output_chunks(output)
        return "".join(chunks) if chunks else None
    except Exception:
        return None


def extract_finish_reason(response: object) -> Optional[str]:
    reason = getattr(response, "finish_reason", None)
    if isinstance(reason, str):
        return reason
    output = getattr(response, "output", None)
    if isinstance(output, list):
        for item in output:
            fr = getattr(item, "finish_reason", None)
            if fr is None and isinstance(item, dict):
                fr = item.get("finish_reason")
            if isinstance(fr, str):
                return fr
            if isinstance(item, dict):
                usage = item.get("usage", {})
                if isinstance(usage, dict):
                    fr = usage.get("finish_reason")
                    if isinstance(fr, str):
                        return fr
    choices = getattr(response, "choices", None)
    if isinstance(choices, list):
        for choice in choices:
            fr = getattr(choice, "finish_reason", None)
            if fr is None and isinstance(choice, dict):
                fr = choice.get("finish_reason")
            if isinstance(fr, str):
                return fr
    return None


def is_truncated_response(response: object) -> bool:
    reason = extract_finish_reason(response)
    if not reason:
        return False
    reason_norm = str(reason).strip().lower()
    return reason_norm in {"length", "max_tokens", "max_output_tokens", "output_limit"}


def extract_json(response: object, *, default: object | None = None) -> object:
    raw = extract_text(response) or ""
    try:
        return json.loads(raw)
    except Exception:
        return {} if default is None else default


def extract_tool_calls(response: object) -> list[dict]:
    outputs = getattr(response, "output", None) or []
    tool_calls: list[dict] = []
    for item in outputs:
        t = getattr(item, "type", None)
        if not t and isinstance(item, dict):
            t = item.get("type")
        if t != "tool_call":
            continue
        fn = getattr(item, "function", None) or (item.get("function") if isinstance(item, dict) else None)
        call_id = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else None)
        name = getattr(fn, "name", None) if fn is not None else None
        if name is None and isinstance(fn, dict):
            name = fn.get("name")
        args_raw = getattr(fn, "arguments", None) if fn is not None else None
        if args_raw is None and isinstance(fn, dict):
            args_raw = fn.get("arguments")
        tool_calls.append({"id": call_id, "name": name, "args_raw": args_raw})
    return tool_calls
