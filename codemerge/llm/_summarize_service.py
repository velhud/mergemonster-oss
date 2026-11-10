from __future__ import annotations

import concurrent.futures
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from codemerge.core.normalize import canonical_path
from codemerge.filemerger.types import FileProcessorLike as FileProcessor
from codemerge.llm.clients import groq_available, get_groq_client, supports_batch_api
from codemerge.llm.config import DEFAULT_GROQ_DECIDER_MODEL, SUMMARY_PROMPT_OVERHEAD
from codemerge.llm.rate_limiter import get_rate_limiter, reserve_tokens
from codemerge.logger import get_logger, log_event

from ._chunking import _chunk_text, _format_chunk_content
from ._limits import SUMMARY_MODEL, resolve_max_output_tokens, summary_model_caps
from ._responses import extract_text, is_truncated_response
from ._retry import extract_retry_after_seconds, is_retryable_error
from ._summarize_prompts import (
    COMPARE_PROMPT,
    SYSTEM_PROMPT,
    _escape_braces,
    build_file_chunk_prompt,
    build_file_merge_prompt,
    build_file_summary_prompt,
    file_prompt_fingerprint,
)
from ._tokens import safe_count_tokens
from .accounting import record_provider_call

logger = get_logger("summarizer")


class TruncationError(RuntimeError):
    pass


def _summary_chunk_tokens(model_name: str, max_output_tokens: int) -> int:
    context_window, _ = summary_model_caps(model_name)
    max_output = max(1, int(max_output_tokens or 0))
    max_budget = max(1024, context_window - max_output - SUMMARY_PROMPT_OVERHEAD)
    chunk_tokens = 20_000
    if max_budget > 0 and chunk_tokens > max_budget:
        return max_budget
    return chunk_tokens


def _assert_within_context(
    *,
    prompt_tokens: int,
    max_output_tokens: int,
    context_window: int,
    label: str,
) -> None:
    if prompt_tokens + max_output_tokens > context_window:
        raise RuntimeError(
            f"{label} exceeds context window: prompt_tokens={prompt_tokens} "
            f"+ max_output_tokens={max_output_tokens} > context_window={context_window}"
        )


def _truncate_log_value(value: object, limit: int = 2000) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _extract_error_details(error: Exception) -> dict:
    details: dict = {}
    status = getattr(error, "status_code", None)
    if status is not None:
        details["status_code"] = status
    failed = getattr(error, "failed_generation", None)
    if failed is None:
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            failed = body.get("failed_generation") or body.get("error", {}).get("failed_generation")
    if failed is not None:
        details["failed_generation"] = failed
    return details


def _resolve_request_timeout_seconds(cfg: Optional[Dict]) -> float | None:
    default_timeout = 120.0
    if not isinstance(cfg, dict):
        return default_timeout
    raw = cfg.get("groq_request_timeout_seconds", default_timeout)
    try:
        timeout = float(raw)
    except Exception:
        return default_timeout
    if timeout <= 0:
        return None
    return timeout


def _resolve_retry_attempts(cfg: Optional[Dict], *, default_attempts: int = 3) -> int:
    attempts_default = max(1, int(default_attempts or 1))
    if not isinstance(cfg, dict):
        return attempts_default
    raw_budget = cfg.get("groq_summary_retry_budget")
    if raw_budget in (None, ""):
        return attempts_default
    try:
        retry_budget = int(raw_budget)
    except Exception:
        return attempts_default
    return max(1, retry_budget + 1)


def _retry_sleep_seconds(error: Exception, *, attempt_index: int) -> float:
    retry_after = extract_retry_after_seconds(error)
    if retry_after is not None:
        return max(0.0, retry_after + 0.25)
    return min(2.0, 0.25 * (2 ** max(0, attempt_index - 1)))


def _safe_relpath(path: str, root: str | None) -> str:
    if not root:
        return str(path)
    try:
        rel = os.path.relpath(path, root)
    except Exception:
        rel = path
    rel = str(rel).replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    return rel


def _repo_name_for_file(path: str, root: str | None) -> str:
    if root:
        try:
            return Path(root).name
        except Exception:
            pass
    try:
        return Path(path).resolve().parent.name
    except Exception:
        return ""


def _resolve_chunk_tokens(cfg: Optional[Dict], *, max_output_tokens: int) -> int:
    _ = cfg
    context_window, _ = summary_model_caps(SUMMARY_MODEL)
    max_budget = max(1024, context_window - max_output_tokens - SUMMARY_PROMPT_OVERHEAD)
    chunk_tokens = 20_000
    if max_budget > 0 and chunk_tokens > max_budget:
        return max_budget
    return chunk_tokens


def _format_chunk_extract_blocks(items: Sequence[tuple[str, str]]) -> str:
    parts = []
    for label, text in items:
        parts.append(f"### {label}\n{str(text or '').strip()}")
    return "\n\n".join(parts).strip() if parts else "(none)"


def _build_unifier_prompt(
    *,
    kind: str,
    profile: str,
    repo_name: str,
    file_rel: str,
    chunk_total: int,
    chunk_items: Sequence[tuple[str, str]],
) -> str:
    blocks = _format_chunk_extract_blocks(chunk_items)
    return build_file_merge_prompt(
        kind,
        profile=profile,
        repo_name=repo_name,
        file_rel=file_rel,
        chunk_total=chunk_total,
        chunk_extract_blocks=blocks,
    )


def _unify_chunk_summaries(
    *,
    file_path: str,
    file_rel: str,
    repo_name: str,
    profile: str,
    kind: str,
    chunk_summaries: list[str],
    app_config: dict,
    token_counter: Callable[[str], int],
    log_context: dict | None,
    max_output_tokens: int,
) -> Optional[str]:
    from codemerge.llm.reducer import reduce_items_to_one_summary

    items = [(f"CHUNK {idx + 1}", text) for idx, text in enumerate(chunk_summaries)]
    chunk_total = len(chunk_summaries)

    def _prompt_builder(group: Sequence[tuple[str, str]]) -> str:
        return _build_unifier_prompt(
            kind=kind,
            profile=profile,
            repo_name=repo_name,
            file_rel=file_rel,
            chunk_total=chunk_total,
            chunk_items=group,
        )

    base_log_context = dict(log_context or {})

    def _summarize(prompt_text: str, *, log_context: dict | None = None) -> str:
        merged = dict(base_log_context)
        if log_context:
            merged.update(log_context)
        return fetch_ai_text_summary(
            prompt_text=prompt_text,
            app_config=app_config,
            token_counter=token_counter,
            log_context=merged or None,
            max_output_tokens_override=max_output_tokens,
        )

    try:
        summary_text, _layers, _counts = reduce_items_to_one_summary(
            items,
            prompt_builder=_prompt_builder,
            summarize_fn=_summarize,
            token_counter=token_counter,
        )
    except Exception:
        return None
    return summary_text


def fetch_ai_text_summary(
    *,
    prompt_text: str,
    app_config: dict,
    token_counter: Optional[Callable[[str], int]] = None,
    log_context: Optional[dict] = None,
    max_output_tokens_override: int | None = None,
    model_name_override: str | None = None,
    system_prompt_override: str | None = None,
    retries: int = 3,
) -> str:
    model_name = str(model_name_override or app_config.get("groq_summary_model") or SUMMARY_MODEL)
    if not model_name:
        model_name = SUMMARY_MODEL
    retries = _resolve_retry_attempts(app_config, default_attempts=retries)
    request_timeout = _resolve_request_timeout_seconds(app_config)
    if not groq_available():
        logger.error("Groq client unavailable. Summaries unavailable.")
        raise RuntimeError("Groq client unavailable. Summaries unavailable.")
    try:
        client = get_groq_client()
    except Exception as exc:
        raise RuntimeError(f"Unable to initialize Groq client: {exc}") from exc

    limiter = get_rate_limiter(model_name, "long_only")
    context_window, model_max_output = summary_model_caps(model_name)
    if max_output_tokens_override is not None:
        try:
            max_output_tokens = int(max_output_tokens_override)
        except Exception:
            max_output_tokens = 0
    else:
        max_output_tokens = 0
    if max_output_tokens <= 0:
        max_output_tokens = resolve_max_output_tokens(app_config, "aggregate", model_name=model_name)
    desired_max_output_tokens = max_output_tokens

    system_prompt = str(system_prompt_override or SYSTEM_PROMPT or "").strip()
    if not system_prompt:
        system_prompt = "You are a professional code summarizer."
    system_tokens = safe_count_tokens(
        system_prompt,
        token_counter=token_counter,
        fallback_scale=1,
        use_tiktoken=False,
    )

    def _request(active_prompt: str, *, attempt: int) -> str:
        prompt_tokens = safe_count_tokens(
            active_prompt,
            token_counter=token_counter,
            fallback_scale=1,
            use_tiktoken=False,
        )
        total_prompt_tokens = prompt_tokens + system_tokens
        available_output = max(1, context_window - total_prompt_tokens)
        if model_max_output > 0:
            available_output = min(available_output, model_max_output)
        if max_output_tokens_override is not None and desired_max_output_tokens > 0:
            max_output_tokens_local = min(desired_max_output_tokens, available_output)
        else:
            max_output_tokens_local = available_output
        reserved_tokens = reserve_tokens(total_prompt_tokens)
        if log_context is not None:
            log_event(
                logger,
                "aggregate_request",
                **log_context,
                attempt=attempt,
                model=model_name,
                prompt_tokens=total_prompt_tokens,
                reserved_tokens=reserved_tokens,
            )
        _assert_within_context(
            prompt_tokens=total_prompt_tokens,
            max_output_tokens=max_output_tokens_local,
            context_window=context_window,
            label="aggregate request",
        )
        limiter.acquire(reserved_tokens)
        request_payload = {
            "model": model_name,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": active_prompt},
            ],
            "max_output_tokens": max_output_tokens_local,
            "temperature": 0,
            "top_p": 1,
            "parallel_tool_calls": False,
        }
        if request_timeout is not None:
            request_payload["timeout"] = request_timeout
        record_provider_call(provider="groq", operation="responses.create", model=model_name)
        response = client.responses.create(**request_payload)
        if log_context is not None:
            log_event(
                logger,
                "aggregate_response",
                **log_context,
                attempt=attempt,
                response_id=getattr(response, "id", None),
            )
        if is_truncated_response(response):
            raise TruncationError("summary truncated (finish_reason=length)")
        raw_output = extract_text(response)
        if not raw_output:
            raise ValueError("Empty summary response")
        return raw_output

    for attempt in range(retries):
        try:
            raw_output = _request(prompt_text, attempt=attempt + 1)
        except TruncationError as exc:
            if log_context is not None:
                log_event(
                    logger,
                    "aggregate_truncated",
                    **log_context,
                    attempt=attempt + 1,
                    error=_truncate_log_value(exc),
                )
            raise
        except Exception as exc:
            retryable = is_retryable_error(exc)
            if log_context is not None:
                details = _extract_error_details(exc)
                log_event(
                    logger,
                    "aggregate_error",
                    **log_context,
                    attempt=attempt + 1,
                    retryable=retryable,
                    error=_truncate_log_value(exc),
                    **{k: _truncate_log_value(v) for k, v in details.items()},
                )
            if retryable and attempt < retries - 1:
                time.sleep(_retry_sleep_seconds(exc, attempt_index=attempt + 1))
                continue
            break
        cleaned = str(raw_output or "").strip()
        if cleaned:
            return cleaned
    raise RuntimeError("aggregate summary error: retries exhausted")


def fetch_ai_object_summary(
    *,
    prompt_text: str,
    json_schema: dict,
    app_config: dict,
    token_counter: Optional[Callable[[str], int]] = None,
    log_context: Optional[dict] = None,
    error_sink: Optional[dict] = None,
    max_output_tokens_override: int | None = None,
    model_name_override: str | None = None,
) -> str:
    """Legacy wrapper that now returns plain-text summaries."""
    _ = json_schema
    _ = error_sink
    text = fetch_ai_text_summary(
        prompt_text=prompt_text,
        app_config=app_config,
        token_counter=token_counter,
        log_context=log_context,
        max_output_tokens_override=max_output_tokens_override,
        model_name_override=model_name_override,
    )
    return text


def fetch_ai_summaries(
    file_paths: List[str],
    file_processor: FileProcessor,
    content_mode: str,
    max_workers: Optional[int] = None,
    app_config: Optional[Dict] = None,
    root: Optional[str] = None,
    *,
    use_batch: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    return_debug: bool = False,
) -> Tuple[Dict[str, Optional[str]], List[str]] | Tuple[
    Dict[str, Optional[str]], List[str], Dict[str, dict]
    ]:
    """Generate AI summaries for the given files."""
    cfg = dict(app_config or {})
    logger.info("fetch_ai_summaries start for %d files", len(file_paths))
    model_name = str(cfg.get("groq_summary_model") or SUMMARY_MODEL)
    if not model_name:
        model_name = SUMMARY_MODEL
    file_max_output_tokens = resolve_max_output_tokens(cfg, "file", model_name=model_name)
    unifier_max_output_tokens = resolve_max_output_tokens(cfg, "unifier", model_name=model_name)
    chunk_tokens = _resolve_chunk_tokens(cfg, max_output_tokens=file_max_output_tokens)
    context_window, _ = summary_model_caps(model_name)
    max_output_tokens = file_max_output_tokens
    if content_mode in {"code_only", "skeleton_only", "code_plus_skeleton"}:
        return {}, []

    if content_mode not in {
        "code_plus_short",
        "code_plus_long",
        "short_only",
        "long_only",
        "short_plus_skeleton",
        "long_plus_skeleton",
        "code_plus_short_plus_skeleton",
        "code_plus_long_plus_skeleton",
    }:
        logger.debug("Unknown content_mode '%s', defaulting to short", content_mode)
        content_mode = "code_plus_short"

    prompt_version = int(cfg.get("summary_prompt_version", 1) or 1)
    kind = "short" if content_mode in {
        "code_plus_short",
        "short_only",
        "short_plus_skeleton",
        "code_plus_short_plus_skeleton",
    } else "long"
    prompt_fp = file_prompt_fingerprint(kind, prompt_version, model=model_name)
    profile = str(cfg.get("summary_profile", "merge") or "merge")
    root_abs = canonical_path(root) if isinstance(root, str) and root else None
    system_prompt = SYSTEM_PROMPT or "You are a professional code summarizer."
    system_tokens = safe_count_tokens(
        system_prompt,
        token_counter=file_processor.count_tokens,
        fallback_scale=1,
        use_tiktoken=False,
    )

    file_context: Dict[str, dict] = {}
    for fpath in file_paths:
        abs_path = canonical_path(fpath)
        file_rel = _safe_relpath(abs_path, root_abs) if root_abs else Path(abs_path).name
        try:
            size_bytes = os.path.getsize(abs_path)
        except Exception:
            size_bytes = 0
        file_context[fpath] = {
            "file_abs": abs_path,
            "file_rel": file_rel,
            "file_ext": Path(abs_path).suffix or "",
            "file_size_bytes": size_bytes,
            "repo_name": _repo_name_for_file(abs_path, root_abs),
        }

    summaries: Dict[str, Optional[str]] = {}
    files_with_errors: List[str] = []
    debug_info: Dict[str, dict] = {}
    if not groq_available():
        logger.error("Groq client unavailable. Summaries unavailable.")
        raise RuntimeError("Groq client unavailable. Summaries unavailable.")
    try:
        client = get_groq_client()
    except Exception as exc:
        raise RuntimeError(f"Unable to initialize Groq client: {exc}") from exc

    limiter = get_rate_limiter(model_name, content_mode)
    request_timeout = _resolve_request_timeout_seconds(cfg)
    default_request_attempts = _resolve_retry_attempts(cfg, default_attempts=3)

    def _request_summary_array(
        prompt_text: str,
        *,
        label: str,
        retries: int = 3,
        log_context: Optional[dict] = None,
        error_sink: Optional[dict] = None,
    ) -> Optional[str]:
        _ = error_sink
        attempts = _resolve_retry_attempts(cfg, default_attempts=retries)
        for attempt in range(attempts):
            try:
                logger.info("summarizing %s (attempt %d)", label, attempt + 1)
                prompt_tokens = safe_count_tokens(
                    prompt_text,
                    token_counter=file_processor.count_tokens,
                    fallback_scale=1,
                    use_tiktoken=False,
                )
                prompt_tokens += system_tokens
                reserved_tokens = reserve_tokens(prompt_tokens)
                if log_context is not None:
                    log_event(
                        logger,
                        "summary_request",
                        **log_context,
                        attempt=attempt + 1,
                        model=model_name,
                        prompt_tokens=prompt_tokens,
                        reserved_tokens=reserved_tokens,
                    )
                _assert_within_context(
                    prompt_tokens=prompt_tokens,
                    max_output_tokens=max_output_tokens,
                    context_window=context_window,
                    label=f"summary request {label}",
                )
                limiter.acquire(reserved_tokens)
                request_payload = {
                    "model": model_name,
                    "input": [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {"role": "user", "content": prompt_text},
                    ],
                    "max_output_tokens": max_output_tokens,
                    "temperature": 0,
                    "top_p": 1,
                    "parallel_tool_calls": False,
                }
                if request_timeout is not None:
                    request_payload["timeout"] = request_timeout
                record_provider_call(provider="groq", operation="responses.create", model=model_name)
                response = client.responses.create(**request_payload)
                if log_context is not None:
                    log_event(
                        logger,
                        "summary_response",
                        **log_context,
                        attempt=attempt + 1,
                        response_id=getattr(response, "id", None),
                    )
                if is_truncated_response(response):
                    raise TruncationError("summary truncated (finish_reason=length)")
                raw_output = extract_text(response)
                if not raw_output:
                    raise ValueError("Empty summary response")
                return str(raw_output).strip()
            except TruncationError as ex:
                if log_context is not None:
                    details = _extract_error_details(ex)
                    log_event(
                        logger,
                        "summary_truncated",
                        **log_context,
                        attempt=attempt + 1,
                        error=_truncate_log_value(ex),
                        **{k: _truncate_log_value(v) for k, v in details.items()},
                    )
                return None
            except Exception as ex:
                retryable = is_retryable_error(ex)
                if log_context is not None:
                    details = _extract_error_details(ex)
                    log_event(
                        logger,
                        "summary_error",
                        **log_context,
                        attempt=attempt + 1,
                        retryable=retryable,
                        error=_truncate_log_value(ex),
                        **{k: _truncate_log_value(v) for k, v in details.items()},
                    )
                logger.warning("Attempt %d failed for %s: %s", attempt + 1, label, ex)
                if retryable and attempt < attempts - 1:
                    time.sleep(_retry_sleep_seconds(ex, attempt_index=attempt + 1))
                    continue
                return None
        return None

    def _fetch_ai_summaries_batch(
        client: object,
        paths: List[str],
        fp: FileProcessor,
        mode: str,
        kind: str,
        profile: str,
        file_ctx: Dict[str, dict],
        model: str,
        progress_cb: Optional[Callable[[int, int], None]],
        summaries: Dict[str, Optional[str]],
        errors: List[str],
        debug: Dict[str, dict],
    ) -> Tuple[Dict[str, Optional[str]], List[str]]:
        logger.info("batch summarization start for %d files", len(paths))

        def _mark_batch_error(fpath: str, reason: str) -> None:
            debug.setdefault(fpath, {})["error"] = reason
            errors.append(fpath)
            logger.warning("summary batch error for %s: %s", fpath, reason)

        id_map: Dict[str, tuple[str, int]] = {}
        chunk_counts: Dict[str, int] = {}
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".ndjson", encoding="utf-8") as tmp:
            for idx, fpath in enumerate(paths):
                content_original = fp.read_file_content(fpath)
                if content_original is None:
                    summaries[fpath] = None
                    _mark_batch_error(fpath, "missing_content")
                    debug[fpath] = {
                        "error": "missing_content",
                        "chunk_tokens": chunk_tokens,
                        "chunk_count": 0,
                        "output_cap": max_output_tokens,
                    }
                    continue
                chunks, chunk_debug = _chunk_text(
                    fp,
                    content_original,
                    chunk_tokens=chunk_tokens,
                    file_path=fpath,
                )
                chunk_counts[fpath] = len(chunks)
                debug[fpath] = {
                    "chunk_tokens": chunk_tokens,
                    "chunk_count": len(chunks),
                    "chunk_lengths": chunk_debug.get("chunk_lengths", []),
                    "coverage_ok": chunk_debug.get("coverage_ok", True),
                    "coverage_mode": chunk_debug.get("coverage_mode", ""),
                    "output_cap": max_output_tokens,
                }
                if not chunk_debug.get("coverage_ok", True):
                    summaries[fpath] = None
                    _mark_batch_error(fpath, "coverage_mismatch")
                    debug[fpath]["error"] = "coverage_mismatch"
                    continue
                ctx = file_ctx.get(fpath, {})
                if len(chunks) == 1:
                    prompt_text = build_file_summary_prompt(
                        kind,
                        profile=profile,
                        repo_name=ctx.get("repo_name", ""),
                        file_rel=ctx.get("file_rel", fpath),
                        file_abs=ctx.get("file_abs", fpath),
                        file_ext=ctx.get("file_ext", ""),
                        file_size_bytes=ctx.get("file_size_bytes", 0),
                        file_content=content_original,
                    )
                    prompt_tokens = safe_count_tokens(
                        prompt_text,
                        token_counter=fp.count_tokens,
                        fallback_scale=1,
                        use_tiktoken=False,
                    )
                    prompt_tokens += system_tokens
                    _assert_within_context(
                        prompt_tokens=prompt_tokens,
                        max_output_tokens=max_output_tokens,
                        context_window=context_window,
                        label=f"summary request batch {fpath}",
                    )
                    body = {
                        "model": model,
                        "input": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt_text},
                        ],
                        "max_output_tokens": max_output_tokens,
                        "temperature": 0,
                        "top_p": 1,
                        "parallel_tool_calls": False,
                    }
                    cid = f"f{idx}_c0"
                    id_map[cid] = (fpath, 0)
                    logger.info("queued %s for batch summarization", fpath)
                    tmp.write(
                        json.dumps(
                            {
                                "custom_id": cid,
                                "method": "POST",
                                "url": "/v1/responses",
                                "body": body,
                            }
                        )
                        + "\n"
                    )
                    continue
                for chunk_idx, chunk in enumerate(chunks):
                    content_for_prompt = _format_chunk_content(
                        fpath,
                        chunk,
                        chunk_idx,
                        len(chunks),
                    )
                    chunk_hint = (
                        "top of file"
                        if chunk_idx == 0
                        else "end of file"
                        if chunk_idx == len(chunks) - 1
                        else "middle of file"
                    )
                    prompt_text = build_file_chunk_prompt(
                        kind,
                        profile=profile,
                        repo_name=ctx.get("repo_name", ""),
                        file_rel=ctx.get("file_rel", fpath),
                        chunk_index=chunk_idx + 1,
                        chunk_total=len(chunks),
                        chunk_hint=chunk_hint,
                        chunk_content=content_for_prompt,
                    )
                    prompt_tokens = safe_count_tokens(
                        prompt_text,
                        token_counter=fp.count_tokens,
                        fallback_scale=1,
                        use_tiktoken=False,
                    )
                    prompt_tokens += system_tokens
                    _assert_within_context(
                        prompt_tokens=prompt_tokens,
                        max_output_tokens=max_output_tokens,
                        context_window=context_window,
                        label=f"summary request batch {fpath} chunk {chunk_idx + 1}",
                    )
                    body = {
                        "model": model,
                        "input": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt_text},
                        ],
                        "max_output_tokens": max_output_tokens,
                        "temperature": 0,
                        "top_p": 1,
                        "parallel_tool_calls": False,
                    }
                    cid = f"f{idx}_c{chunk_idx}"
                    id_map[cid] = (fpath, chunk_idx)
                    logger.info(
                        "queued %s (chunk %d/%d) for batch summarization",
                        fpath,
                        chunk_idx + 1,
                        len(chunks),
                    )
                    tmp.write(
                        json.dumps(
                            {
                                "custom_id": cid,
                                "method": "POST",
                                "url": "/v1/responses",
                                "body": body,
                            }
                        )
                        + "\n"
                    )
        ndjson_path = tmp.name
        try:
            with open(ndjson_path, "rb") as fh:
                record_provider_call(provider="groq", operation="files.create", model=model_name)
                input_file = client.files.create(file=fh, purpose="batch")
            record_provider_call(provider="groq", operation="batches.create", model=model_name)
            batch = client.batches.create(
                input_file_id=input_file.id,
                endpoint="/v1/responses",
                completion_window="24h",
            )
            logger.info("submitted batch %s", batch.id)
            total = len(id_map)
            while batch.status not in {"completed", "failed", "canceled", "cancelled", "expired"}:
                if progress_cb:
                    rc = getattr(batch, "request_counts", None)
                    if rc:
                        if isinstance(rc, dict):
                            completed = rc.get("completed", rc.get("succeeded", 0))
                            total_req = rc.get("total", total)
                        else:
                            completed = getattr(rc, "completed", 0) or getattr(rc, "succeeded", 0)
                            total_req = getattr(rc, "total", total)
                        progress_cb(completed, total_req)
                time.sleep(5)
                record_provider_call(provider="groq", operation="batches.retrieve", model=model_name)
                batch = client.batches.retrieve(batch.id)
            if batch.status != "completed":
                raise RuntimeError(f"Batch failed with status {batch.status}")
            logger.info("batch %s completed", batch.id)
            record_provider_call(provider="groq", operation="files.content", model=model_name)
            result = client.files.content(batch.output_file_id)
            content = (
                result.text
                if hasattr(result, "text")
                else result.read().decode("utf-8")
            )
            chunk_results: Dict[str, Dict[int, Optional[str]]] = {}
            for line in content.splitlines():
                data = json.loads(line)
                cid = data.get("custom_id")
                meta = id_map.get(cid)
                if not meta:
                    continue
                fpath, chunk_idx = meta
                body = data.get("response", {}).get("body", {})
                summary = body.get("output_text") if isinstance(body, dict) else None
                if summary:
                    chunk_results.setdefault(fpath, {})[chunk_idx] = str(summary).strip()
                    logger.info("summary ok for %s chunk %d", fpath, chunk_idx + 1)
                else:
                    chunk_results.setdefault(fpath, {})[chunk_idx] = None
                    logger.error("no summary returned for %s chunk %d", fpath, chunk_idx + 1)
            for fpath in paths:
                if fpath in summaries:
                    continue
                expected = chunk_counts.get(fpath, 0)
                chunk_map = chunk_results.get(fpath, {})
                if expected and len(chunk_map) == expected and all(
                    chunk_map.get(i) is not None for i in range(expected)
                ):
                    ordered = [chunk_map[i] or "" for i in range(expected)]
                    if expected == 1:
                        summaries[fpath] = ordered[0]
                        debug.setdefault(fpath, {})["unifier_ran"] = False
                    else:
                        ctx = file_ctx.get(fpath, {})
                        unified = _unify_chunk_summaries(
                            file_path=fpath,
                            file_rel=ctx.get("file_rel", fpath),
                            repo_name=ctx.get("repo_name", ""),
                            profile=profile,
                            kind=kind,
                            chunk_summaries=ordered,
                            app_config=cfg,
                            token_counter=fp.count_tokens,
                            log_context={
                                "path": canonical_path(fpath),
                                "prompt_fp": prompt_fp,
                                "kind": kind,
                                "phase": "file_unifier",
                                "chunk_total": expected,
                            },
                            max_output_tokens=unifier_max_output_tokens,
                        )
                        if unified is None:
                            summaries[fpath] = None
                            _mark_batch_error(fpath, "unifier_failed")
                            logger.error("unifier failed for %s", fpath)
                        else:
                            summaries[fpath] = unified
                            debug.setdefault(fpath, {})["unifier_ran"] = True
                else:
                    summaries[fpath] = None
                    _mark_batch_error(fpath, "chunk_incomplete")
            if progress_cb:
                progress_cb(total, total)
        finally:
            try:
                os.remove(ndjson_path)
            except Exception:
                pass
        logger.info("fetch_ai_summaries complete (batch)")
        return summaries, errors

    if use_batch:
        if not supports_batch_api():
            logger.warning("Groq Batch API unsupported; falling back to threaded summarization")
        else:
            result = _fetch_ai_summaries_batch(
                client,
                file_paths,
                file_processor,
                content_mode,
                kind,
                profile,
                file_context,
                model_name,
                progress_callback,
                summaries,
                files_with_errors,
                debug_info,
            )
            if return_debug:
                return result[0], result[1], debug_info
            return result

    def _autotune_parallelism() -> int:
        rpm = getattr(limiter, "rpm", 120)
        tpm = getattr(limiter, "tpm", rpm * 400)
        context_tokens = getattr(limiter, "context_window", None)
        tokens_per_request = chunk_tokens
        if context_tokens and context_tokens > 0:
            tokens_per_request = min(tokens_per_request, context_tokens)
        tokens_per_request = max(1, tokens_per_request)

        # Assume each worker comfortably submits ~30 requests per minute (roughly
        # two requests per second end-to-end). This keeps throughput near the
        # provider ceiling without requiring hundreds of threads.
        request_based = max(4, math.ceil(rpm / 30))

        # Token pacing: ensure we have enough parallelism to keep the token
        # budget saturated even for large files.
        token_based = max(4, max(1, tpm // tokens_per_request))

        return max(request_based, token_based)

    def _summarize_chunk_with_retries(
        fpath: str,
        chunk: str,
        chunk_idx: int,
        total_chunks: int,
        retries: int,
        ctx: dict,
    ) -> Optional[str]:
        content_for_prompt = _format_chunk_content(
            fpath,
            chunk,
            chunk_idx,
            total_chunks,
        )
        chunk_hint = (
            "top of file"
            if chunk_idx == 0
            else "end of file"
            if chunk_idx == total_chunks - 1
            else "middle of file"
        )
        prompt_text = build_file_chunk_prompt(
            kind,
            profile=profile,
            repo_name=ctx.get("repo_name", ""),
            file_rel=ctx.get("file_rel", fpath),
            chunk_index=chunk_idx + 1,
            chunk_total=total_chunks,
            chunk_hint=chunk_hint,
            chunk_content=content_for_prompt,
        )
        label = f"{fpath} chunk {chunk_idx + 1}/{total_chunks}"
        log_context = {
            "path": canonical_path(fpath),
            "prompt_fp": prompt_fp,
            "kind": kind,
            "chunk_index": chunk_idx + 1,
            "chunk_total": total_chunks,
            "chunk_tokens": chunk_tokens,
        }
        summary = _request_summary_array(
            prompt_text,
            label=label,
            retries=retries,
            log_context=log_context,
            error_sink=debug_info.get(fpath),
        )
        if summary is None:
            logger.error(
                "Failed to summarize %s chunk %d/%d after %d attempts",
                fpath,
                chunk_idx + 1,
                total_chunks,
                _resolve_retry_attempts(cfg, default_attempts=retries),
            )
            return None
        logger.info(
            "summary ok for %s chunk %d/%d",
            fpath,
            chunk_idx + 1,
            total_chunks,
        )
        return summary

    def fetch_summary_with_retries(
        fpath: str, retries: int = 3
    ) -> Optional[str]:
        def _fail(reason: str) -> None:
            info = debug_info.setdefault(fpath, {})
            info["error"] = reason
            logger.warning("summary failed for %s: %s", fpath, reason)

        content_original = file_processor.read_file_content(fpath)
        if content_original is None:
            debug_info[fpath] = {
                "error": "missing_content",
                "chunk_tokens": chunk_tokens,
                "chunk_count": 0,
            }
            return None
        chunks, chunk_debug = _chunk_text(
            file_processor,
            content_original,
            chunk_tokens=chunk_tokens,
            file_path=fpath,
        )
        total_chunks = len(chunks)
        ctx = file_context.get(fpath, {})
        debug_info[fpath] = {
            "chunk_tokens": chunk_tokens,
            "chunk_count": total_chunks,
            "chunk_lengths": chunk_debug.get("chunk_lengths", []),
            "coverage_ok": chunk_debug.get("coverage_ok", True),
            "coverage_mode": chunk_debug.get("coverage_mode", ""),
            "output_cap": max_output_tokens,
        }
        if not chunk_debug.get("coverage_ok", True):
            _fail("coverage_mismatch")
            return None
        if total_chunks == 1:
            prompt_text = build_file_summary_prompt(
                kind,
                profile=profile,
                repo_name=ctx.get("repo_name", ""),
                file_rel=ctx.get("file_rel", fpath),
                file_abs=ctx.get("file_abs", fpath),
                file_ext=ctx.get("file_ext", ""),
                file_size_bytes=ctx.get("file_size_bytes", 0),
                file_content=content_original,
            )
            label = f"{fpath} (full)"
            log_context = {
                "path": canonical_path(fpath),
                "prompt_fp": prompt_fp,
                "kind": kind,
                "chunk_index": 1,
                "chunk_total": 1,
                "chunk_tokens": chunk_tokens,
            }
            summary = _request_summary_array(
                prompt_text,
                label=label,
                retries=retries,
                log_context=log_context,
                error_sink=debug_info.get(fpath),
            )
            if summary is None:
                _fail("summary_failed")
                return None
            debug_info[fpath]["unifier_ran"] = False
            return summary
        chunk_items: list[str] = []
        for chunk_idx, chunk in enumerate(chunks):
            summary = _summarize_chunk_with_retries(
                fpath,
                chunk,
                chunk_idx,
                total_chunks,
                retries,
                ctx,
            )
            if summary is None:
                _fail("chunk_failed")
                return None
            chunk_items.append(summary)
        if len(chunk_items) != total_chunks:
            _fail("chunk_incomplete")
            return None
        if total_chunks == 1:
            debug_info[fpath]["unifier_ran"] = False
            return chunk_items[0]
        unified = _unify_chunk_summaries(
            file_path=fpath,
            file_rel=ctx.get("file_rel", fpath),
            repo_name=ctx.get("repo_name", ""),
            profile=profile,
            kind=kind,
            chunk_summaries=chunk_items,
            app_config=cfg,
            token_counter=file_processor.count_tokens,
            log_context={
                "path": canonical_path(fpath),
                "prompt_fp": prompt_fp,
                "kind": kind,
                "phase": "file_unifier",
                "chunk_total": total_chunks,
            },
            max_output_tokens=unifier_max_output_tokens,
        )
        if unified is None:
            _fail("unifier_failed")
            return None
        debug_info[fpath]["unifier_ran"] = True
        return unified

    # Determine the number of worker threads. A value of ``0`` or ``None``
    # means auto-scaling based on the rate limits for the selected model.
    # A minimum of one worker is always used.
    worker_key = "groq_max_workers"
    ABSOLUTE_WORKER_CAP = 64
    cfg_workers = 0
    if max_workers is None or max_workers == 0:
        try:
            cfg_workers = int(cfg.get(worker_key, 0))
        except Exception:
            cfg_workers = 0
        if cfg_workers > 0:
            max_workers = cfg_workers
        else:
            cpu = os.cpu_count() or 4
            cpu_cap = max(4, cpu * 2)
            limit_cap = _autotune_parallelism()
            auto_cap = max(cpu_cap, limit_cap)
            max_workers = min(len(file_paths), auto_cap, ABSOLUTE_WORKER_CAP)
    max_workers = max(1, max_workers)
    logger.info(
        "threaded summarization using %d workers", max_workers,
        extra={"backend": "groq"},
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {
            executor.submit(fetch_summary_with_retries, path, default_request_attempts): path
            for path in file_paths
        }
        completed = 0
        total = len(future_to_path)
        if progress_callback:
            progress_callback(0, total)
        for future in concurrent.futures.as_completed(future_to_path):
            fpath = future_to_path[future]
            try:
                result = future.result()
                summaries[fpath] = result
                if result is None:
                    files_with_errors.append(fpath)
            except Exception:
                logger.exception(f"Error fetching summary for file {fpath}")
                debug_info.setdefault(fpath, {})["error"] = "exception"
                summaries[fpath] = None
                files_with_errors.append(fpath)
            finally:
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)
    logger.info("fetch_ai_summaries complete")
    if files_with_errors:
        files_with_errors = list(dict.fromkeys(files_with_errors))
    if return_debug:
        return summaries, files_with_errors, debug_info
    return summaries, files_with_errors


def compare_files_ai(
    content_a: str, content_b: str, app_config: Optional[Dict] = None
) -> str:
    """Compare two files with Groq and decide which one to keep."""
    if not groq_available():
        logger.error("Groq client unavailable. Comparison unavailable.")
        raise RuntimeError("Groq client unavailable. Comparison unavailable.")

    try:
        client = get_groq_client()
    except Exception as exc:
        raise RuntimeError(f"Unable to initialize Groq client: {exc}") from exc

    model_name = (
        app_config.get("groq_decider_model", DEFAULT_GROQ_DECIDER_MODEL)
        if app_config is not None
        else DEFAULT_GROQ_DECIDER_MODEL
    )
    prompt_text = COMPARE_PROMPT.format(
        file_a=_escape_braces(content_a),
        file_b=_escape_braces(content_b),
    )
    # Instruct the model to return only a single verdict token describing
    # which file should be kept, not which one is newer.
    context_window, max_output_tokens = summary_model_caps(model_name)
    prompt_tokens = max(len(prompt_text), 1)
    _assert_within_context(
        prompt_tokens=prompt_tokens,
        max_output_tokens=max_output_tokens,
        context_window=context_window,
        label="compare_files_ai",
    )
    record_provider_call(provider="groq", operation="responses.create", model=model_name)
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a strict diff judge. Compare two code files and "
                    "respond with exactly one token: NO_DIFFERENCE, USE_A, "
                    "USE_B, or UNSURE. Return only that token."
                ),
            },
            {"role": "user", "content": prompt_text},
        ],
        max_output_tokens=max_output_tokens,
        temperature=0,
        top_p=1,
        reasoning={"effort": "medium", "summary": "auto"},
        timeout=_resolve_request_timeout_seconds(app_config if isinstance(app_config, dict) else {}),
    )

    msg = extract_text(response) or ""
    verdict_tokens = msg.strip().split() if msg else []
    verdict = verdict_tokens[0] if verdict_tokens else "UNSURE"
    logger.debug("compare_files_ai verdict=%s", verdict)
    return verdict
