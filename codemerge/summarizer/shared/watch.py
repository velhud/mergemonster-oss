from __future__ import annotations

import os
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from codemerge.logger import get_logger, log_event
from codemerge.models import FileMode
from codemerge.filemerger.git import git_vendored_set
from codemerge.metrics import EVENT as M_EVENT, emit as metric_emit

from .orchestrator import ensure_summaries

logger = get_logger(__name__)


def _normalize_plan(plan: Dict[str, FileMode] | Dict[str, str]) -> Dict[str, FileMode]:
    out: Dict[str, FileMode] = {}
    for path, mode in plan.items():
        abs_path = os.path.abspath(path)
        if isinstance(mode, FileMode):
            out[abs_path] = mode
            continue
        normalized = str(mode).upper()
        if normalized in FileMode.__members__:
            out[abs_path] = FileMode[normalized]
        elif normalized in {m.value for m in FileMode}:
            out[abs_path] = FileMode(normalized)
        else:
            out[abs_path] = FileMode.CODE
    return out


def _partition_modes(
    norm_plan: Dict[str, FileMode]
) -> Tuple[List[str], List[str], List[str], List[str], List[str], List[str]]:
    exclude: List[str] = []
    code_only: List[str] = []
    summary_short_only: List[str] = []
    summary_long_only: List[str] = []
    code_plus_short: List[str] = []
    code_plus_long: List[str] = []

    for path, mode in norm_plan.items():
        if mode is FileMode.EXCLUDE:
            exclude.append(path)
        elif mode is FileMode.CODE:
            code_only.append(path)
        elif mode in (FileMode.CODE_PLUS_SHORT, FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON):
            code_plus_short.append(path)
        elif mode in (FileMode.CODE_PLUS_LONG, FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON):
            code_plus_long.append(path)
        elif mode in (FileMode.SUMMARY_SHORT, FileMode.SUMMARY_SHORT_PLUS_SKELETON):
            summary_short_only.append(path)
        elif mode in (FileMode.SUMMARY_LONG, FileMode.SUMMARY_LONG_PLUS_SKELETON):
            summary_long_only.append(path)
        else:
            code_only.append(path)

    return (
        exclude,
        code_only,
        summary_short_only,
        summary_long_only,
        code_plus_short,
        code_plus_long,
    )


def _filter_vendored(
    directory: str,
    paths: Iterable[str],
    *,
    summarize_vendored: bool,
) -> Tuple[List[str], int]:
    items = list(paths)
    if summarize_vendored:
        return items, 0

    rel_paths: List[str] = []
    rel_to_abs: Dict[str, str] = {}
    for abs_path in items:
        try:
            rel_path = os.path.relpath(abs_path, directory)
        except Exception:
            continue
        rel_paths.append(rel_path)
        rel_to_abs[rel_path] = abs_path

    vendored = git_vendored_set(directory, rel_paths)
    filtered = [rel_to_abs[rel] for rel in rel_paths if rel not in vendored]
    skipped = len(items) - len(filtered)
    return filtered, skipped


def tick(
    directory: str,
    plan: Dict[str, FileMode] | Dict[str, str],
    file_processor,
    app_config: Dict,
    *,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, object]:
    """Ensure summaries for plan entries requesting summary output."""

    cfg = dict(app_config or {})
    cfg["auto_refresh_summaries"] = True
    cfg["auto_summarize_missing"] = True

    norm_plan = _normalize_plan(plan)
    (
        exclude,
        _code,
        summary_short_only,
        summary_long_only,
        code_plus_short,
        code_plus_long,
    ) = _partition_modes(norm_plan)

    summarize_vendored = bool(cfg.get("summarize_vendored", False))

    summary_short_targets = summary_short_only + code_plus_short
    summary_long_targets = summary_long_only + code_plus_long

    summary_short, skipped_v_short = _filter_vendored(
        directory, summary_short_targets, summarize_vendored=summarize_vendored
    )
    summary_long, skipped_v_long = _filter_vendored(
        directory, summary_long_targets, summarize_vendored=summarize_vendored
    )

    summaries: Dict[str, Optional[str]] = {}
    errors_total = 0
    ensured_short = ensured_long = 0
    errors_short = errors_long = 0
    error_paths: set[str] = set()
    requested_paths: set[str] = set(summary_short + summary_long)

    if summary_short:
        try:
            fetched, errors = ensure_summaries(
                summary_short, "short", file_processor, cfg, progress_cb, root=directory
            )
        except TypeError:
            fetched, errors = ensure_summaries(
                summary_short, "short", file_processor, cfg, progress_cb
            )
        for path, items in fetched.items():
            abs_path = os.path.abspath(path)
            summaries[path] = items
            summaries[abs_path] = items
        ensured_short = len(fetched)
        errors_short = len(errors)
        errors_total += errors_short
        for err in errors:
            try:
                error_paths.add(os.path.abspath(err))
            except Exception:
                error_paths.add(err)

    if summary_long:
        try:
            fetched, errors = ensure_summaries(
                summary_long, "long", file_processor, cfg, progress_cb, root=directory
            )
        except TypeError:
            fetched, errors = ensure_summaries(
                summary_long, "long", file_processor, cfg, progress_cb
            )
        for path, items in fetched.items():
            abs_path = os.path.abspath(path)
            summaries[path] = items
            summaries[abs_path] = items
        ensured_long = len(fetched)
        errors_long = len(errors)
        errors_total += errors_long
        for err in errors:
            try:
                error_paths.add(os.path.abspath(err))
            except Exception:
                error_paths.add(err)

    metrics = {
        "requested_short": len(summary_short_targets),
        "requested_long": len(summary_long_targets),
        "requested_code_plus_short": len(code_plus_short),
        "requested_code_plus_long": len(code_plus_long),
        "skipped_vendored": skipped_v_short + skipped_v_long,
        "skipped_excluded": len(exclude),
        "ensured": len({os.path.abspath(p) for p in summaries.keys()}),
        "ensured_short": ensured_short,
        "ensured_long": ensured_long,
        "errors": errors_total,
        "errors_short": errors_short,
        "errors_long": errors_long,
    }

    metric_emit(M_EVENT["WATCH_TICK"], root=os.path.abspath(directory), **metrics)
    log_event(
        logger,
        M_EVENT["WATCH_TICK"],
        **metrics,
        root=os.path.abspath(directory),
    )

    return {
        "summaries": summaries,
        "metrics": metrics,
        "requested_paths": list(requested_paths),
        "error_paths": list(error_paths),
    }


def loop(
    directory: str,
    plan: Dict[str, FileMode] | Dict[str, str],
    file_processor,
    app_config: Dict,
    *,
    interval_sec: Optional[int] = None,
    stop_flag: Optional[Callable[[], bool]] = None,
    on_tick: Optional[Callable[[Dict[str, object]], None]] = None,
) -> None:
    """Continuously invoke :func:`tick` until ``stop_flag`` returns True."""

    interval = int(interval_sec or app_config.get("watch_interval_sec", 3) or 3)
    interval = max(interval, 1)

    while True:
        result = tick(directory, plan, file_processor, app_config)
        if on_tick:
            try:
                on_tick(result)
            except Exception:
                logger.exception("watch_loop on_tick callback failed")
        if stop_flag and stop_flag():
            break
        time.sleep(interval)
