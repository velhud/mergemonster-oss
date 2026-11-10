from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from codemerge.core import ASSET_EXT, BINARY_EXT
from codemerge.logger import get_logger, log_event
from codemerge.metrics import EVENT as M_EVENT, emit as metric_emit
from codemerge.core.normalize import canonical_path
from codemerge.llm.summarize import (
    fetch_ai_summaries,
    file_prompt_fingerprint,
    resolve_summary_model,
)
from codemerge.summarizer.shared.change_detect import GitCtx, bulk_partition, file_sha256
from codemerge.summarizer.shared.summary_cache import SummaryCache

logger = get_logger(__name__)

# Summarization invariants:
# - Summary model defaults to groq_summary_model config when provided.
# - Full file-content coverage via chunking.
# - Plain-text summaries; no schema enforcement.
# - Deterministic sampling parameters for all summary calls.

# ───────────────────────────── utils ─────────────────────────────


def _norm_path(p: str) -> str:
    return canonical_path(p)


def _as_text(summary_text: str) -> str:
    return str(summary_text or "")


def _from_text(summary_text: str) -> str:
    return str(summary_text or "")


def _stat(path: str) -> Tuple[float, int]:
    st = os.stat(path)
    return float(st.st_mtime), int(st.st_size)


def _resolve_git_root(paths_abs: List[str], root_hint: Optional[str]) -> Optional[str]:
    candidates: List[Path] = []
    if isinstance(root_hint, str) and root_hint:
        candidates.append(Path(root_hint))
    if paths_abs:
        try:
            common = os.path.commonpath(paths_abs)
            if common:
                candidates.append(Path(common))
        except Exception:
            pass
    for cand in candidates:
        try:
            cand = cand.resolve()
        except Exception:
            pass
        if cand.is_file():
            cand = cand.parent
        for parent in (cand, *cand.parents):
            if (parent / ".git").exists():
                return str(parent)
    return None


def _apply_backoff(row: Dict) -> bool:
    """
    Return True if we should SKIP re-trying now due to backoff.
    Exponential in minutes, capped at 60 minutes.
    """

    status = str(row.get("status", "ok")).lower()
    if status == "ok":
        return False
    err = int(row.get("error_count", 0))
    upd = float(row.get("updated_at", 0.0))
    delay_min = min(60, 2 ** max(0, min(err, 10)))
    return (time.time() - upd) < delay_min * 60


# ─────────────────────────── public API ───────────────────────────


def ensure_summaries(
    file_paths: List[str],
    kind: str,  # 'short' | 'long'
    file_processor,  # codemerge.filemerger.FileProcessor
    app_config: Dict,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    *,
    force: bool = False,
    root: Optional[str] = None,
    strict_required: bool = False,
) -> Tuple[Dict[str, Optional[str]], List[str]]:
    """
    Ensure fresh summaries for the given files.

    Returns:
      - summaries_by_file: abs path -> summary text or None if unavailable
      - files_with_errors: list of abs paths that failed during refresh
    """

    original_paths: List[str] = list(file_paths)
    norm_map: Dict[str, str] = {}
    paths_abs: List[str] = []
    for p in original_paths:
        abs_p = _norm_path(p)
        paths_abs.append(abs_p)
        norm_map.setdefault(abs_p, p)
    total = len(paths_abs)
    if total == 0:
        return {}, []

    model = resolve_summary_model(app_config)
    version = int(app_config.get("summary_prompt_version", 1))
    strategy = str(app_config.get("summary_change_strategy", "mtime_size"))
    auto_missing = bool(app_config.get("auto_summarize_missing", True))
    auto_refresh = bool(app_config.get("auto_refresh_summaries", True))
    if strict_required:
        auto_missing = True
        auto_refresh = True

    git_ctx = None
    if strategy == "git":
        root_hint = root if isinstance(root, str) and root else app_config.get("last_directory")
        git_root = _resolve_git_root(paths_abs, root_hint if isinstance(root_hint, str) else None)
        if git_root:
            git_ctx = GitCtx(
                git_root,
                since_ref=str(app_config.get("git_since_ref", "")),
                until_ref=str(app_config.get("git_until_ref", "HEAD")),
                include_untracked=bool(app_config.get("git_include_untracked", False)),
            )

    fp = file_prompt_fingerprint(kind, version, model=model)
    cache = SummaryCache(app_config.get("summary_cache_db_path"))
    try:
        cache.purge_stale_prompt_fp(fp, model=model, kind=kind)
        cached = cache.bulk_get(paths_abs, model, kind, fp)

        # Partition cache state
        fresh, stale, missing = bulk_partition(
            paths_abs, cached, strategy=strategy, git_ctx=git_ctx
        )
        stale_error: List[str] = []
        stale_ok: List[str] = []
        for abs_path in stale:
            row = cached.get(abs_path)
            if row and str(row.get("status", "ok")).lower() != "ok":
                stale_error.append(abs_path)
            else:
                stale_ok.append(abs_path)

        out: Dict[str, Optional[str]] = {}

        def _load_cached(abs_path: str) -> Optional[str]:
            row = cached.get(abs_path)
            if not row or not row.get("summary_text"):
                return None
            try:
                return _from_text(row["summary_text"])
            except Exception:
                return None

        for abs_path in fresh:
            key = norm_map.get(abs_path, abs_path)
            out[key] = _load_cached(abs_path)
            metric_emit(M_EVENT["SUMMARY_CACHE_HIT"], path=key, kind=kind)
            log_event(logger, "summary_cache_hit", path=key, kind=kind)

        for abs_path in stale:
            key = norm_map.get(abs_path, abs_path)
            if not strict_required and key not in out:
                out[key] = _load_cached(abs_path)
            metric_emit(M_EVENT["SUMMARY_CACHE_STALE"], path=key, kind=kind)
            log_event(logger, "summary_cache_stale", path=key, kind=kind)

        for abs_path in missing:
            key = norm_map.get(abs_path, abs_path)
            metric_emit(M_EVENT["SUMMARY_CACHE_MISS"], path=key, kind=kind)
            log_event(logger, "summary_cache_miss", path=key, kind=kind)

        # Respect auto flags or force override
        if force:
            needs = list(dict.fromkeys(paths_abs))
        else:
            needs: List[str] = []
            if stale_error:
                needs.extend(stale_error)
            if auto_refresh and stale_ok:
                needs.extend(stale_ok)
            if auto_missing and missing:
                needs.extend([p for p in missing if p not in needs])

            # Apply backoff for entries with prior errors
            if needs and not strict_required:
                filtered: List[str] = []
                for abs_path in needs:
                    row = cached.get(abs_path)
                    if row and _apply_backoff(row):
                        log_event(
                            logger,
                            "summary_backoff_skip",
                            path=norm_map.get(abs_path, abs_path),
                            error_count=row.get("error_count", 0),
                        )
                        continue
                    filtered.append(abs_path)
                needs = filtered

        files_with_errors: List[str] = []
        skipped_paths: set[str] = set()
        skipped_count = 0

        def _mark_skip(abs_path: str, key: str, reason: str, *, count: bool) -> None:
            nonlocal skipped_count
            out.setdefault(key, None)
            skipped_paths.add(abs_path)
            if count:
                skipped_count += 1

        def _filter_needs(candidates: List[str], *, count_skips: bool) -> List[str]:
            filtered: List[str] = []
            for abs_path in candidates:
                key = norm_map.get(abs_path, abs_path)
                ext = (Path(abs_path).suffix or "").lower()
                if ext in BINARY_EXT or ext in ASSET_EXT:
                    _mark_skip(abs_path, key, "binary_ext", count=count_skips)
                    log_event(logger, "summary_skip_binary_ext", path=key)
                    continue
                try:
                    if file_processor.is_binary_file(abs_path):
                        _mark_skip(abs_path, key, "binary_content", count=count_skips)
                        log_event(logger, "summary_skip_binary_content", path=key)
                        continue
                except Exception:
                    # Fail-safe: assume binary
                    _mark_skip(abs_path, key, "binary_check_failed", count=count_skips)
                    log_event(logger, "summary_skip_binary_check_failed", path=key)
                    continue

                content = file_processor.read_file_content(abs_path)
                if content is None:
                    _mark_skip(abs_path, key, "unreadable", count=count_skips)
                    log_event(logger, "summary_skip_unreadable", path=key)
                    continue

                filtered.append(abs_path)

            return filtered

        needs = _filter_needs(needs, count_skips=True)

        completed_base = len(fresh) + skipped_count
        if progress_cb:
            progress_cb(completed_base, total)

        def _fetch_and_store(
            targets: List[str],
            *,
            completed_base: int,
            use_batch_override: Optional[bool] = None,
            emit_progress: bool = True,
        ) -> None:
            if not targets:
                return
            mode = "short_only" if kind.lower().startswith("short") else "long_only"

            def _inner_prog(done: int, tot: int) -> None:
                if progress_cb and emit_progress:
                    progress_cb(completed_base + done, total)

            use_batch = (
                bool(app_config.get("groq_use_batch", False))
                if use_batch_override is None
                else bool(use_batch_override)
            )
            try:
                try:
                    result = fetch_ai_summaries(
                        targets,
                        file_processor,
                        mode,
                        max_workers=None,
                        app_config=app_config,
                        root=root,
                        use_batch=use_batch,
                        progress_callback=_inner_prog if emit_progress else None,
                        return_debug=True,
                    )
                except TypeError:
                    result = fetch_ai_summaries(
                        targets,
                        file_processor,
                        mode,
                        max_workers=None,
                        app_config=app_config,
                        root=root,
                        use_batch=use_batch,
                        progress_callback=_inner_prog if emit_progress else None,
                    )

                if isinstance(result, tuple) and len(result) == 3:
                    summaries, errors, _debug_info = result
                elif isinstance(result, tuple) and len(result) == 2:
                    summaries, errors = result
                else:
                    raise RuntimeError("fetch_ai_summaries returned unexpected result")
            except Exception:
                log_event(logger, "summary_fetch_failed", kind=kind, count=len(targets))
                summaries = {}
                errors = list(targets)
                if progress_cb and emit_progress:
                    progress_cb(total, total)

            # Persist results to cache and fill out[]
            for abs_path in targets:
                try:
                    mtime, size = _stat(abs_path)
                except Exception:
                    mtime, size = 0.0, 0
                try:
                    digest = file_sha256(abs_path)
                except Exception:
                    digest = None
                summary_text = summaries.get(abs_path)
                key = norm_map.get(abs_path, abs_path)
                if summary_text is not None:
                    out[key] = summary_text
                    try:
                        cache.put_ok(
                            abs_path,
                            mtime=mtime,
                            size=size,
                            model=model,
                            kind=kind,
                            prompt_fp=fp,
                            summary_text=_as_text(summary_text),
                            sha256=digest,
                        )
                    except Exception:
                        pass
                    metric_emit(M_EVENT["SUMMARY_PUT_OK"], path=key, kind=kind)
                    log_event(logger, "summary_put_ok", path=key, kind=kind)
                else:
                    if key not in files_with_errors:
                        files_with_errors.append(key)
                    try:
                        cache.put_error(
                            abs_path,
                            model=model,
                            kind=kind,
                            prompt_fp=fp,
                            mtime=mtime,
                            size=size,
                            sha256=digest,
                        )
                    except Exception:
                        pass
                    metric_emit(M_EVENT["SUMMARY_PUT_ERROR"], path=key, kind=kind)
                    log_event(logger, "summary_put_error", path=key, kind=kind)

            # Merge reported errors (normalised)
            for err_path in errors:
                key = norm_map.get(_norm_path(err_path), _norm_path(err_path))
                if key not in files_with_errors:
                    files_with_errors.append(key)

        # Fetch for 'needs'
        if needs:
            _fetch_and_store(needs, completed_base=completed_base, emit_progress=True)
        else:
            # No fetch. Ensure progress is complete if callback exists.
            if progress_cb and completed_base < total:
                progress_cb(total, total)

        # For paths neither fresh nor fetched (due to flags/backoff), ensure keys exist with None
        for abs_p in paths_abs:
            out.setdefault(norm_map.get(abs_p, abs_p), None)

        if strict_required:
            missing_abs = [
                abs_p
                for abs_p in paths_abs
                if out.get(norm_map.get(abs_p, abs_p)) is None
            ]
            retry_abs = [p for p in missing_abs if p not in skipped_paths]
            if retry_abs:
                retry_needs = _filter_needs(retry_abs, count_skips=False)
                if retry_needs:
                    log_event(
                        logger,
                        "summary_retry_missing",
                        kind=kind,
                        count=len(retry_needs),
                    )
                    _fetch_and_store(
                        retry_needs,
                        completed_base=max(total - len(retry_needs), 0),
                        use_batch_override=False,
                        emit_progress=False,
                    )
                missing_abs = [
                    abs_p
                    for abs_p in paths_abs
                    if out.get(norm_map.get(abs_p, abs_p)) is None
                ]
            if missing_abs:
                missing_required = [norm_map.get(abs_p, abs_p) for abs_p in missing_abs]
                raise RuntimeError(
                    "Strict summary required but unavailable for: "
                    + ", ".join(missing_required)
                )

        return out, files_with_errors
    finally:
        try:
            cache.close()
        except Exception:
            pass


def load_cached_summaries(
    file_paths: Iterable[str],
    kind: str,
    app_config: Dict,
) -> Dict[str, Optional[str]]:
    """
    Convenience: read cached summaries without fetching.
    """

    original_paths: List[str] = list(file_paths)
    norm_map: Dict[str, str] = {}
    paths_abs: List[str] = []
    for p in original_paths:
        abs_p = _norm_path(p)
        paths_abs.append(abs_p)
        norm_map.setdefault(abs_p, p)
    model = resolve_summary_model(app_config)
    version = int(app_config.get("summary_prompt_version", 1))
    fp = file_prompt_fingerprint(kind, version, model=model)

    cache = SummaryCache(app_config.get("summary_cache_db_path"))
    try:
        cached = cache.bulk_get(paths_abs, model, kind, fp)
        out: Dict[str, Optional[str]] = {}
        for abs_p in paths_abs:
            row = cached.get(abs_p)
            if row and row.get("status") == "ok" and row.get("summary_text"):
                try:
                    out[norm_map.get(abs_p, abs_p)] = _from_text(row["summary_text"])
                    continue
                except Exception:
                    pass
            out[norm_map.get(abs_p, abs_p)] = None
        return out
    finally:
        try:
            cache.close()
        except Exception:
            pass


__all__ = ["ensure_summaries", "load_cached_summaries"]
