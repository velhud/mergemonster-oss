from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..types import FileProcessorLike as FileProcessor
from ...models import FileMeta, ScanContext
from .dto import UiState

log = logging.getLogger(__name__)

DEFAULT_CONTENT_MAX_KB = 256
DEFAULT_DUPLICATE_HASH_KB = 256


def compute_noise_exclusions(base_excludes: Iterable[str], config: Dict[str, Any]) -> Set[str]:
    """Expand exclusion flags into concrete directory names.

    This centralises the definition of "noise" directories so UIs and agents
    share a single mapping from config flags -> excluded dir names.
    """

    def _get_bool(key: str, default: bool) -> bool:
        val = config.get(key, default)
        if val is None:
            return default
        try:
            return bool(val)
        except Exception:
            return default

    excluded = {os.path.normpath(str(d)) for d in (base_excludes or []) if d}

    if _get_bool("exclude_node_modules", True):
        excluded.add("node_modules")

    if _get_bool("exclude_ios_noise", True):
        excluded |= {
            os.path.normpath(d)
            for d in (
                "DerivedData",
                "Pods",
                "Carthage",
                ".build",
                ".swiftpm",
                "xcuserdata",
                "xcshareddata",
            )
        }

    if _get_bool("exclude_python_noise", False):
        excluded |= {
            os.path.normpath(d)
            for d in (
                ".venv",
                "venv",
                "__pycache__",
                ".mypy_cache",
                ".pytest_cache",
                "build",
                "dist",
                ".tox",
            )
        }

    if _get_bool("exclude_java_gradle_noise", False):
        excluded |= {os.path.normpath(d) for d in (".gradle", "build", "out", "target")}

    if _get_bool("exclude_web_build_noise", False):
        excluded |= {
            os.path.normpath(d)
            for d in ("dist", "build", ".next", ".nuxt", ".angular", "coverage")
        }

    return excluded


def _always_include(meta: FileMeta, ui: UiState) -> bool:
    if not ui.filters.always_include_docs:
        return False
    name = meta.rel_path.name.lower()
    stem = meta.rel_path.stem.lower()
    patterns = [p.lower() for p in ui.filters.always_include_list]
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            try:
                if fnmatch.fnmatchcase(name, pat):
                    return True
            except Exception:
                continue
        else:
            if stem.startswith(pat):
                return True
    return False


def always_include(meta: FileMeta, ui: UiState) -> bool:
    """Expose always-include rule for other consumers (e.g. UI explanations)."""
    return _always_include(meta, ui)


def _content_match(path: Path, needle: str, regex: bool, case: bool, max_bytes: int) -> bool:
    try:
        limit = max(0, max_bytes)
        with open(path, "rb") as fh:
            buf = fh.read(limit) if limit else b""
    except Exception:
        return False

    try:
        text = buf.decode("utf-8", errors="replace")
    except Exception:
        return False

    if not regex:
        haystack = text if case else text.lower()
        needle_cmp = needle if case else needle.lower()
        return needle_cmp in haystack

    flags = 0 if case else re.IGNORECASE
    try:
        return re.search(needle, text, flags) is not None
    except re.error:
        # Fallback to substring search when regex is invalid
        haystack = text if case else text.lower()
        needle_cmp = needle if case else needle.lower()
        return needle_cmp in haystack


def _duplicate_cache_key(meta: FileMeta, max_bytes: int) -> tuple[str, int, int, int]:
    path = os.path.abspath(str(meta.abs_path))
    mtime_ns = int(meta.mtime * 1_000_000_000)
    return (path, int(meta.size_bytes), mtime_ns, int(max_bytes))


def _duplicate_digest(
    meta: FileMeta,
    max_bytes: int,
    cache: Optional[Dict[tuple[str, int, int, int], str]] = None,
) -> Optional[str]:
    key = _duplicate_cache_key(meta, max_bytes)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    path = key[0]
    try:
        limit = max(0, max_bytes)
        with open(path, "rb") as fh:
            head = fh.read(limit) if limit else fh.read()
        digest = hashlib.blake2b(head, digest_size=16).hexdigest()
    except Exception:
        return None
    if cache is not None:
        cache[key] = digest
    return digest


def _compute_duplicate_set(
    metas: Sequence[FileMeta],
    max_bytes: int,
    cache: Optional[Dict[tuple[str, int, int, int], str]] = None,
) -> Set[str]:
    dup_set: Set[str] = set()
    seen: Dict[str, str] = {}
    for meta in metas:
        path = os.path.abspath(str(meta.abs_path))
        digest = _duplicate_digest(meta, max_bytes, cache)
        if digest is None:
            continue
        key = f"{meta.size_bytes}:{digest}"
        if key in seen:
            dup_set.add(path)
        else:
            seen[key] = path
    return dup_set


def _dedupe_visible(
    metas: List[FileMeta],
    max_bytes: int,
    forced: Optional[Set[str]] = None,
    cache: Optional[Dict[tuple[str, int, int, int], str]] = None,
) -> List[FileMeta]:
    seen: Dict[str, str] = {}
    output: List[FileMeta] = []
    forced_lookup = {os.path.abspath(p) for p in (forced or set())}
    for meta in metas:
        path = os.path.abspath(str(meta.abs_path))
        digest = _duplicate_digest(meta, max_bytes, cache)
        if digest is None:
            output.append(meta)
            continue
        key = f"{meta.size_bytes}:{digest}"
        if key in seen:
            if path in forced_lookup:
                output.append(meta)
            continue
        seen[key] = path
        output.append(meta)
    return output


def _name_pattern_match(rel: str, pattern: str, use_glob: bool, compiled: Optional[re.Pattern]) -> bool:
    if not pattern:
        return True
    if use_glob:
        parts = [p.strip() for p in re.split(r"[;,]", pattern) if p.strip()]
        if not parts:
            return True
        rel_cmp = rel.lower()
        parts_cmp = [part.lower() for part in parts]
        return any(fnmatch.fnmatchcase(rel_cmp, part) for part in parts_cmp)
    if compiled is None:
        return True
    try:
        return compiled.search(rel) is not None
    except Exception:
        return False


def _sort_files(
    files: List[FileMeta],
    ui: UiState,
    sort_override: Optional[Tuple[str, bool]],
) -> List[FileMeta]:
    key_func = None
    reverse = False

    if sort_override:
        column, reverse = sort_override
        column = (column or "").lower()
        key_map = {
            "path": lambda m: m.rel_path.as_posix().lower(),
            "ext": lambda m: m.ext,
            "size": lambda m: m.size_bytes,
            "tok": lambda m: m.token_count or 0,
            "dens": lambda m: ((m.token_count or 0) / max(m.size_bytes / 1024.0, 0.001)),
            "cat": lambda m: m.category.value,
        }
        key_func = key_map.get(column, key_map["path"])
    else:
        mode = (ui.scan.sort_by or "").lower()
        reverse = bool(ui.scan.sort_desc)
        if mode == "tokens":
            def key_func(m: FileMeta) -> int:
                return m.token_count or 0
        elif mode == "token_density":
            def key_func(m: FileMeta) -> float:
                return (m.token_count or 0) / max(m.size_bytes / 1024.0, 0.001)
        elif mode == "size":
            def key_func(m: FileMeta) -> int:
                return m.size_bytes
        elif mode == "date":
            def key_func(m: FileMeta) -> float:
                return m.mtime
        else:  # default name sort
            def key_func(m: FileMeta) -> str:
                return m.rel_path.as_posix().lower()

    try:
        return sorted(files, key=key_func, reverse=reverse)
    except Exception:
        log.exception("sorting failed; falling back to path order")
        return sorted(files, key=lambda m: m.rel_path.as_posix().lower())


def apply_filters(
    ctx: ScanContext,
    ui: UiState,
    fp: FileProcessor,
    git_changed: Set[str] | None,
    vendored: Set[str] | None,
    *,
    xcode_sources: Set[str] | None = None,
    sort_override: Optional[Tuple[str, bool]] = None,
    token_cache: Optional[Dict[Tuple[str, bool, bool], int]] = None,
    duplicate_hash_cache: Optional[Dict[Tuple[str, int, int, int], str]] = None,
    forced_includes: Optional[Set[str]] = None,
) -> Tuple[List[FileMeta], Dict[str, Tuple[int, int]], Set[str], List[str], Set[str]]:
    """Apply UI filters to the scanned files and return view-ready data."""

    warnings: List[str] = []
    token_cache = token_cache if token_cache is not None else {}

    pattern = ui.filters.name_pattern.strip() if ui.filters.name_pattern else ""
    compiled_pattern: Optional[re.Pattern] = None
    if pattern and not ui.filters.name_glob:
        try:
            compiled_pattern = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            warnings.append(f"Name pattern ignored: {exc}")
            compiled_pattern = None

    def _resolve_max_bytes(value_kb: int, default_kb: int) -> int:
        try:
            kb = int(value_kb)
        except Exception:
            kb = 0
        if kb <= 0:
            kb = default_kb
        return kb * 1024

    content_bytes = _resolve_max_bytes(
        int(ui.filters.content_max_kb or 0), DEFAULT_CONTENT_MAX_KB
    )
    dup_kb = int(getattr(ui.filters, "duplicate_hash_max_kb", 0) or 0)
    dup_bytes = _resolve_max_bytes(dup_kb, DEFAULT_DUPLICATE_HASH_KB)

    dup_set: Set[str] = set()
    if ui.filters.mark_duplicates:
        dup_set = _compute_duplicate_set(ctx.files, dup_bytes, duplicate_hash_cache)

    visible: List[FileMeta] = []
    mark_pattern_hits = bool(pattern)
    pattern_hits: Set[str] = set()
    pattern_cache: Dict[str, bool] = {}
    forced_lookup = {os.path.abspath(p) for p in (forced_includes or set())}
    forced_visible: Set[str] = set()
    for meta in ctx.files:
        abs_str = os.path.abspath(str(meta.abs_path))
        is_forced = abs_str in forced_lookup
        is_always = _always_include(meta, ui)
        rel_str = meta.rel_path.as_posix()
        if meta.excluded and not is_forced:
            continue

        if git_changed is not None and rel_str not in git_changed and not (is_forced or is_always):
            continue

        if vendored is not None and rel_str in vendored and not (is_forced or is_always):
            continue

        if not ui.filters.category_mask.get(meta.category, True) and not (is_forced or is_always):
            continue

        token_val = meta.token_count or 0
        if not (is_forced or is_always) and ui.filters.apply_token_filters_to_transforms:
            key = (str(meta.abs_path), bool(ui.filters.enumerate_lines), bool(ui.filters.remove_empty))
            if key in token_cache:
                token_val = token_cache[key]
            else:
                try:
                    token_val = fp.processed_token_count(
                        str(meta.abs_path),
                        enumerate_lines=ui.filters.enumerate_lines,
                        remove_empty=ui.filters.remove_empty,
                    )
                    token_cache[key] = token_val
                except Exception:
                    # Keep prior token count on failure
                    log.debug("processed_token_count failed for %s", meta.rel_path, exc_info=True)

        if not (is_forced or is_always) and (token_val < ui.filters.token_min or token_val > ui.filters.token_max):
            continue

        size_kb = meta.size_bytes / 1024.0
        if not (is_forced or is_always) and (size_kb < ui.filters.size_min_kb or size_kb > ui.filters.size_max_kb):
            continue

        if mark_pattern_hits:
            matched = pattern_cache.get(rel_str)
            if matched is None:
                matched = _name_pattern_match(rel_str, pattern, ui.filters.name_glob, compiled_pattern)
                pattern_cache[rel_str] = matched
            if matched:
                pattern_hits.add(rel_str)
            if not matched and not (is_forced or is_always):
                continue
        elif not (is_forced or is_always):
            if not _name_pattern_match(rel_str, pattern, ui.filters.name_glob, compiled_pattern):
                continue

        if ui.filters.content_search and not (is_forced or is_always):
            if not _content_match(
                meta.abs_path,
                ui.filters.content_search,
                ui.filters.content_regex,
                ui.filters.content_case,
                content_bytes,
            ):
                continue

        if ui.filters.date_after_ts and meta.mtime < ui.filters.date_after_ts and not (is_forced or is_always):
            continue

        if ui.filters.date_before_ts and meta.mtime > ui.filters.date_before_ts and not (is_forced or is_always):
            continue

        visible.append(meta)
        if is_forced or is_always:
            forced_visible.add(abs_str)

    if xcode_sources is not None and ui.filters.xcode_target and ui.filters.xcode_target != "(All)":
        visible = [
            m
            for m in visible
            if m.rel_path.as_posix() in xcode_sources
            or os.path.abspath(str(m.abs_path)) in forced_visible
        ]

    if ui.filters.hide_duplicates:
        visible = _dedupe_visible(visible, dup_bytes, forced_visible, duplicate_hash_cache)

    ordered = _sort_files(visible, ui, sort_override)

    folder_totals: Dict[str, Tuple[int, int]] = {}
    for meta in ordered:
        parts = list(meta.rel_path.parts)
        for idx in range(1, len(parts)):
            key = "/".join(parts[:idx])
            tok_sum, byte_sum = folder_totals.get(key, (0, 0))
            folder_totals[key] = (tok_sum + (meta.token_count or 0), byte_sum + meta.size_bytes)

    over_limit_count = sum(
        1
        for m in ordered
        if m.token_count
        and m.token_count > ui.filters.token_max
        and os.path.abspath(str(m.abs_path)) not in forced_visible
    )
    if over_limit_count:
        warnings.append(
            f"Warning: {over_limit_count} file(s) exceed the current token-max ({ui.filters.token_max:,})"
        )

    return ordered, folder_totals, dup_set, warnings, pattern_hits

def merge_totals_footer_text(
    fp: FileProcessor,
    ui: UiState,
    view: List[FileMeta],
    *,
    content_mode_map: Optional[Dict[str, str]] = None,
) -> str:
    """Build footer text using the token estimator. Stub for PR1."""
    try:
        cfg = None
        try:
            from ..config import AppConfig

            cfg = AppConfig().get_config()
        except Exception:
            cfg = {}
        fallback_on_error = bool(cfg.get("fallback_to_code_on_summary_error", False))
        strict_required = bool(cfg.get("strict_summary_required", False))
        summary_max_tokens = int(cfg.get("file_summary_max_output_tokens", 0) or 0)

        summaries_short: Dict[str, Optional[str]] | None = None
        summaries_long: Dict[str, Optional[str]] | None = None
        missing_count = 0
        if ui.summary.content_mode != "code_only":
            try:
                from ..render import normalize_mode, summary_flags
                from ...summarizer.shared.orchestrator import load_cached_summaries

                short_required: List[str] = []
                long_required: List[str] = []
                for meta in view:
                    abs_path = str(meta.abs_path)
                    eff = None
                    if content_mode_map:
                        eff = content_mode_map.get(abs_path)
                    mode_norm = normalize_mode(eff)
                    summary_kind, summary_required, _requested_code_plus = summary_flags(
                        mode_norm, ui.summary.content_mode
                    )
                    if not summary_required:
                        continue
                    if summary_kind == "short":
                        short_required.append(abs_path)
                    elif summary_kind == "long":
                        long_required.append(abs_path)

                if short_required:
                    summaries_short = load_cached_summaries(short_required, "short", cfg)
                if long_required:
                    summaries_long = load_cached_summaries(long_required, "long", cfg)

                missing = set()
                for path in short_required:
                    if not summaries_short or summaries_short.get(path) is None:
                        missing.add(path)
                for path in long_required:
                    if not summaries_long or summaries_long.get(path) is None:
                        missing.add(path)
                missing_count = len(missing)
            except Exception:
                summaries_short = None
                summaries_long = None
                missing_count = 0

        files_abs = [str(m.abs_path) for m in view]
        total_with_headers, content_only = fp.merge_group_token_details(
            str(ui.root_dir),
            files_abs,
            include_file_tree=ui.output.include_tree,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            prompt_template=ui.summary.prompt_template,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            content_mode=ui.summary.content_mode,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            content_mode_map=content_mode_map,
            fallback_to_code_on_summary_error=fallback_on_error,
            strict_summary_required=strict_required,
        )
        upper_bound = None
        if missing_count and summary_max_tokens > 0:
            upper_bound = total_with_headers + (missing_count * summary_max_tokens)

        if ui.output.footer_final_only:
            if upper_bound is not None:
                return f"Tokens: {total_with_headers:,} (upper bound: {upper_bound:,})"
            if missing_count:
                return f"Tokens: {total_with_headers:,} (missing summaries: {missing_count})"
            return f"Tokens: {total_with_headers:,}"

        if upper_bound is not None:
            return (
                f"Visible tokens: {content_only:,} (final: {total_with_headers:,}–{upper_bound:,})"
            )
        if missing_count:
            return (
                f"Visible tokens: {content_only:,} (final: {total_with_headers:,}; missing summaries: {missing_count})"
            )
        return f"Visible tokens: {content_only:,} (final: {total_with_headers:,})"
    except Exception:
        if missing_count:
            return "Tokens: unavailable (missing summaries)"
        return "Tokens: -"
