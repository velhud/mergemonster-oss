"""LLM-backed pruning and agent operations."""

from __future__ import annotations

import json
import time
from typing import Callable, Dict, Optional

from ._prune_tools import DELETE_DEFS_TOOL
from ._responses import extract_text, extract_tool_calls
from ._retry import extract_retry_after_seconds, is_rate_limit_error
from ._tokens import safe_count_tokens
from .accounting import record_provider_call
from .clients import groq_available, get_groq_client
from .config import (
    DEFAULT_GROQ_DECIDER_MODEL,
    DEFAULT_GROQ_VERIFIER_MODEL,
)
from .rate_limiter import get_rate_limiter, reserve_tokens
from ..logger import get_logger, log_event
from ..prune.assets import load_json_asset

logger = get_logger(__name__)


def interpret_goal_with_llm(
    goal: str,
    thin_index: list[dict],
    *,
    client: Optional[object],
    cfg: Dict,
) -> dict:
    if client is None:
        if not groq_available():
            if not cfg.get("prune_goal_llm_enabled", False):
                return {}
            raise RuntimeError("Groq client unavailable (set prune_goal_llm_enabled=False to bypass).")
        client = get_groq_client()
    strict = bool(cfg.get("groq_strict_mode", True))
    model_name = cfg.get("groq_decider_model", DEFAULT_GROQ_DECIDER_MODEL) or DEFAULT_GROQ_DECIDER_MODEL
    schema = load_json_asset("schema_goal_plan.json")
    prompt = {
        "role": "user",
        "content": json.dumps({
            "goal": goal,
            "index": thin_index,
            "instructions": "Return strict JSON selecting only necessary files/defs for the goal, and explicit excludes (globs/exts). Prefer precision, avoid tests/tools unless necessary.",
        }),
    }
    format_dict = {
        "type": "json_schema",
        "name": schema["name"],
        "schema": schema["schema"],
    }
    if strict:
        format_dict["strict"] = True
    text_config = {"format": format_dict}

    limiter = get_rate_limiter(model_name, "long_only")
    prompt_text = prompt.get("content", "")
    prompt_tokens = safe_count_tokens(prompt_text)
    reserved = reserve_tokens(prompt_tokens)
    max_retries = 3

    for attempt in range(max_retries):
        limiter.acquire(reserved)
        try:
            record_provider_call(provider="groq", operation="responses.create", model=model_name)
            resp = client.responses.create(
                model=model_name,
                input=[prompt],
                reasoning={"effort": "medium", "summary": "auto"},
                text=text_config,
                max_output_tokens=20_000,
                parallel_tool_calls=False,
            )
            log_event(
                logger,
                "interpret_goal_request",
                model=model_name,
                prompt_tokens=prompt_tokens,
                reserved_tokens=reserved,
                response_id=getattr(resp, "id", None),
            )
            break
        except TypeError as te:
            raise RuntimeError(f"Groq client rejected structured output request: {te}") from te
        except Exception as exc:
            if is_rate_limit_error(exc):
                retry_after = extract_retry_after_seconds(exc)
                if retry_after and attempt < max_retries - 1:
                    logger.warning("429 rate limit hit; sleeping %.1fs before retry", retry_after)
                    time.sleep(retry_after + 0.5)
                    continue
            logger.error("interpret_goal request failed: %s", exc)
            raise
    else:
        raise RuntimeError("interpret_goal max retries exceeded")

    raw = extract_text(resp) or ""
    try:
        data = json.loads(raw)
        for k in ("include_files", "include_defs", "exclude_globs", "exclude_exts"):
            data.setdefault(k, [])
        return data
    except Exception as exc:
        raise RuntimeError(f"Goal interpretation returned invalid JSON: {exc}") from exc


def select_spans_with_llm(
    *,
    goal: str,
    options: list[dict],
    client: Optional[object],
    cfg: Dict,
) -> list[dict]:
    if client is None:
        if not groq_available():
            raise RuntimeError("Groq client not available.")
        client = get_groq_client()
    schema = load_json_asset("schema_span_pick.json")
    prompt = {
        "role": "user",
        "content": json.dumps(
            {
                "goal": goal,
                "options": options,
                "instruction": "From options, return ONLY the minimal defs/classes strictly needed to implement the goal. Ignore tests/tools/unrelated helpers. Prefer precision.",
            }
        ),
    }
    fmt = {"type": "json_schema", "name": schema["name"], "schema": schema["schema"], "strict": True}

    model_name = cfg.get("groq_decider_model", DEFAULT_GROQ_DECIDER_MODEL)
    limiter = get_rate_limiter(model_name, "long_only")
    prompt_text = prompt.get("content", "")
    prompt_tokens = safe_count_tokens(prompt_text)
    reserved = reserve_tokens(prompt_tokens)
    max_retries = 3

    for attempt in range(max_retries):
        limiter.acquire(reserved)
        try:
            record_provider_call(provider="groq", operation="responses.create", model=model_name)
            resp = client.responses.create(
                model=model_name,
                input=[prompt],
                text={"format": fmt},
                parallel_tool_calls=False,
                max_output_tokens=20_000,
                reasoning={"effort": "medium", "summary": "auto"},
            )
            log_event(
                logger,
                "prune_selector_request",
                model=model_name,
                prompt_tokens=prompt_tokens,
                reserved_tokens=reserved,
                response_id=getattr(resp, "id", None),
            )
            break
        except Exception as exc:
            if is_rate_limit_error(exc):
                retry_after = extract_retry_after_seconds(exc)
                if retry_after and attempt < max_retries - 1:
                    logger.warning("429 rate limit hit; sleeping %.1fs before retry", retry_after)
                    time.sleep(retry_after + 0.5)
                    continue
            raise
    else:
        raise RuntimeError("prune selector max retries exceeded")

    data = json.loads(extract_text(resp) or "{}")
    return data.get("keep", []) or []


def drop_spans_with_llm(
    *,
    goal: str,
    spans: list[dict],
    errors: list[str],
    client: Optional[object],
    cfg: Dict,
) -> set[str]:
    if client is None and groq_available():
        client = get_groq_client()
    if not client:
        return set()
    schema = {
        "name": "edits",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"drop": {"type": "array", "items": {"type": "string"}}},
            "required": ["drop"],
            "additionalProperties": False,
        },
    }
    prompt = {
        "role": "user",
        "content": json.dumps(
            {"goal": goal, "spans": spans, "errors": errors},
        ),
    }
    fmt = {"type": "json_schema", "name": schema["name"], "schema": schema["schema"], "strict": True}

    model_name = cfg.get("groq_verifier_model", DEFAULT_GROQ_VERIFIER_MODEL)
    limiter = get_rate_limiter(model_name, "short_only")
    prompt_text = prompt.get("content", "")
    prompt_tokens = safe_count_tokens(prompt_text)
    reserved = reserve_tokens(prompt_tokens)
    max_retries = 3

    for attempt in range(max_retries):
        limiter.acquire(reserved)
        try:
            record_provider_call(provider="groq", operation="responses.create", model=model_name)
            resp = client.responses.create(
                model=model_name,
                input=[prompt],
                text={"format": fmt},
                parallel_tool_calls=False,
                max_output_tokens=4000,
                reasoning={"effort": "low", "summary": "auto"},
            )
            log_event(
                logger,
                "prune_critic_request",
                model=model_name,
                prompt_tokens=prompt_tokens,
                reserved_tokens=reserved,
                response_id=getattr(resp, "id", None),
            )
            data = json.loads(extract_text(resp) or "{}")
            return set(data.get("drop", []) or [])
        except Exception as exc:
            if is_rate_limit_error(exc):
                retry_after = extract_retry_after_seconds(exc)
                if retry_after and attempt < max_retries - 1:
                    logger.warning("429 rate limit hit; sleeping %.1fs before retry", retry_after)
                    time.sleep(retry_after + 0.5)
                    continue
            logger.warning("prune critic request failed: %s", exc)
            return set()
    return set()


def suggest_stubs(
    symbols: list[str],
    *,
    client: Optional[object],
    cfg: Optional[Dict] = None,
) -> dict:
    _ = cfg
    if not symbols:
        return {}
    if client is None:
        if not groq_available():
            logger.warning("Groq client unavailable; suggest_stubs disabled")
            return {}
        client = get_groq_client()
    prompt = (
        "Provide minimal Python stub implementations and a small unittest "
        "scaffold for the following unresolved symbols:\n" + "\n".join(symbols)
    )
    schema = load_json_asset("schema_stub_suggestions.json")
    format_dict = {
        "type": "json_schema",
        "name": schema["name"],
        "schema": schema["schema"],
        "strict": True,
    }
    text_config = {"format": format_dict}

    model_name = DEFAULT_GROQ_VERIFIER_MODEL
    limiter = get_rate_limiter(model_name, "long_only")
    prompt_tokens = safe_count_tokens(prompt)
    reserved = reserve_tokens(prompt_tokens)
    max_retries = 3

    for attempt in range(max_retries):
        limiter.acquire(reserved)
        try:
            logger.info("suggest_stubs request")
            record_provider_call(provider="groq", operation="responses.create", model=model_name)
            resp = client.responses.create(
                model=model_name,
                input=[{"role": "user", "content": prompt}],
                reasoning={"effort": "medium", "summary": "auto"},
                text=text_config,
                max_output_tokens=20000,
                parallel_tool_calls=False,
            )
            log_event(
                logger,
                "suggest_stubs_request",
                model=model_name,
                prompt_tokens=prompt_tokens,
                reserved_tokens=reserved,
                response_id=getattr(resp, "id", None),
            )
            raw = extract_text(resp) or ""
            data = json.loads(raw)
            logger.info("suggest_stubs ok for %s", symbols)
            return data
        except Exception as exc:
            if is_rate_limit_error(exc):
                retry_after = extract_retry_after_seconds(exc)
                if retry_after and attempt < max_retries - 1:
                    logger.warning("429 rate limit hit; sleeping %.1fs before retry", retry_after)
                    time.sleep(retry_after + 0.5)
                    continue
            logger.error("suggest_stubs request failed: %s", exc)
            return {}
    return {}


def synthesize_harness_cases(
    *,
    goal: str,
    options: list[dict],
    client: Optional[object],
    cfg: Dict,
) -> list[str]:
    if client is None and groq_available():
        client = get_groq_client()
    if not client:
        return []
    schema = {
        "name": "harness",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "module": {"type": "string"},
                            "call": {"type": "string"},
                        },
                        "required": ["module", "call"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["cases"],
            "additionalProperties": False,
        },
    }
    prompt = {
        "role": "user",
        "content": json.dumps(
            {
                "goal": goal,
                "options": options,
                "instruction": "Propose minimal run calls to exercise the goal. Use importable module dotted path in 'module' and a Python expression in 'call'. No external IO or network.",
            }
        ),
    }
    fmt = {"type": "json_schema", "name": schema["name"], "schema": schema["schema"], "strict": True}

    model_name = cfg.get("groq_decider_model", DEFAULT_GROQ_DECIDER_MODEL)
    limiter = get_rate_limiter(model_name, "short_only")
    prompt_text = prompt.get("content", "")
    prompt_tokens = safe_count_tokens(prompt_text)
    reserved = reserve_tokens(prompt_tokens)
    max_retries = 3

    for attempt in range(max_retries):
        limiter.acquire(reserved)
        try:
            record_provider_call(provider="groq", operation="responses.create", model=model_name)
            resp = client.responses.create(
                model=model_name,
                input=[prompt],
                text={"format": fmt},
                parallel_tool_calls=False,
                max_output_tokens=4000,
                reasoning={"effort": "low", "summary": "auto"},
            )
            log_event(
                logger,
                "prune_harness_request",
                model=model_name,
                prompt_tokens=prompt_tokens,
                reserved_tokens=reserved,
                response_id=getattr(resp, "id", None),
            )
            data = json.loads(extract_text(resp) or "{}")
            scripts: list[str] = []
            for c in data.get("cases", []):
                m = c.get("module")
                call = c.get("call")
                if not m or not call:
                    continue
                scripts.append(
                    "import importlib\n"
                    f"m=importlib.import_module({m!r})\n"
                    f"res=eval({call!r}, vars(m))\n"
                    "print(type(res).__name__)\n"
                )
            return scripts
        except Exception as exc:
            if is_rate_limit_error(exc):
                retry_after = extract_retry_after_seconds(exc)
                if retry_after and attempt < max_retries - 1:
                    logger.warning("429 rate limit hit; sleeping %.1fs before retry", retry_after)
                    time.sleep(retry_after + 0.5)
                    continue
            break
    return []


def run_pruning_agent_exchange(
    *,
    goal: str,
    index: Dict,
    client: Optional[object],
    cfg: Dict,
    handle_tool_call: Callable[[dict], dict],
) -> object:
    if client is None:
        if not groq_available():
            logger.error("Groq client not available")
            raise RuntimeError("Groq client not available")
        client = get_groq_client()

    model_name = cfg.get("groq_decider_model", DEFAULT_GROQ_DECIDER_MODEL)
    user_input = json.dumps({"goal": goal, "index": index})
    schema = load_json_asset("schema_prune_plan.json")
    format_dict = {
        "type": "json_schema",
        "name": schema["name"],
        "schema": schema["schema"],
        "strict": True,
    }
    text_config = {"format": format_dict}
    try:
        logger.info("pruning request to model %s", model_name)
        record_provider_call(provider="groq", operation="responses.create", model=model_name)
        resp = client.responses.create(
            model=model_name,
            input=[{"role": "user", "content": user_input}],
            tools=[DELETE_DEFS_TOOL],
            reasoning={"effort": "medium", "summary": "auto"},
            text=text_config,
            max_output_tokens=20_000,
            parallel_tool_calls=False,
        )
        logger.info("pruning request ok")
    except Exception as exc:
        logger.error("pruning request failed: %s", exc)
        raise

    while True:
        tool_calls = extract_tool_calls(resp)
        if not tool_calls:
            break
        outputs_payload = []
        for call in tool_calls:
            result = handle_tool_call(call)
            if not isinstance(result, dict):
                result = {"status": "ignored"}
            outputs_payload.append(
                {
                    "tool_call_id": call.get("id"),
                    "output": json.dumps(result),
                }
            )
        try:
            record_provider_call(provider="groq", operation="responses.submit_tool_outputs", model=model_name)
            resp = client.responses.submit_tool_outputs(
                response_id=getattr(resp, "id", None),
                tool_outputs=outputs_payload,
            )
        except Exception as exc:
            logger.error("submitting tool outputs failed: %s", exc)
            raise
    return resp
