from __future__ import annotations

import fnmatch
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .. import FileProcessor
from ..exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)
from ...models import FileMeta
from . import git_service, scan_service
from .dto import UiState


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
    return compiled.search(rel) is not None


def run_diagnostics(
    fp: FileProcessor,
    ui: UiState,
    visible: Sequence[FileMeta],
    *,
    config: Optional[Dict] = None,
) -> str:
    """Generate a textual diagnostics report for the current selection."""

    root = str(ui.root_dir)
    if not root or not os.path.isdir(root):
        raise FileNotFoundError("Select a valid directory before running diagnostics")

    cfg = config or {}
    show_all = bool(cfg.get("_show_all", False))
    ignore_custom = bool(cfg.get("_ignore_custom_excluded", False))
    excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(getattr(fp, "excluded_dirs", set()))
    excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(
        getattr(fp, "excluded_files", set())
    )
    def _norm_custom(value: str) -> str:
        rel = str(value).strip().replace("\\", "/")
        if rel.startswith("./"):
            rel = rel[2:]
        return rel

    custom_excluded = {
        _norm_custom(x)
        for x in (cfg.get("custom_excluded", []) or [])
        if isinstance(x, str) and x.strip()
    }

    include_all = ui.scan.include_all
    ignore_binary = ui.scan.ignore_binary
    max_mb = float(ui.scan.max_file_size_mb or 0.0)
    file_types = ui.file_types or {}
    visible_rel = {meta.rel_path.as_posix() for meta in visible}

    scan_skipped: List[Tuple[str, str]] = []
    filtered_out: List[Tuple[str, str]] = []
    included: List[str] = []

    pattern = ui.filters.name_pattern
    compiled_pattern: Optional[re.Pattern] = None
    if pattern and not ui.filters.name_glob:
        try:
            compiled_pattern = re.compile(pattern, re.IGNORECASE)
        except re.error:
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
        int(ui.filters.content_max_kb or 0),
        scan_service.DEFAULT_CONTENT_MAX_KB,
    )
    dup_kb = int(getattr(ui.filters, "duplicate_hash_max_kb", 0) or 0)
    dup_bytes = _resolve_max_bytes(dup_kb, scan_service.DEFAULT_DUPLICATE_HASH_KB)

    ios_noise = bool(getattr(ui.scan, "exclude_ios_noise", False))
    node_noise = bool(getattr(ui.scan, "exclude_node_modules", False))
    python_noise = bool(getattr(ui.scan, "exclude_python_noise", False))
    java_noise = bool(getattr(ui.scan, "exclude_java_gradle_noise", False))
    web_noise = bool(getattr(ui.scan, "exclude_web_build_noise", False))

    follow_symlinks = bool(ui.scan.follow_symlinks)
    max_depth = int(ui.scan.max_depth or 0)

    extra_excludes: set[str] = set()
    if ios_noise:
        extra_excludes |= {"DerivedData", "Pods", "Carthage", ".build", ".swiftpm", "xcuserdata", "xcshareddata"}
    if node_noise:
        extra_excludes.add("node_modules")
    if python_noise:
        extra_excludes |= {".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "build", "dist", ".tox"}
    if java_noise:
        extra_excludes |= {".gradle", "build", "out", "target"}
    if web_noise:
        extra_excludes |= {"dist", "build", ".next", ".nuxt", ".angular", "coverage"}

    extra_excludes_norm = {norm_path(x) for x in extra_excludes}
    excluded_dirs_rel_effective = set(excluded_dirs_rel) | extra_excludes_norm

    def _scan_reason(full_path: str, rel: str) -> str | None:
        if not full_path:
            return "invalid_path"
        name_norm = norm_path(os.path.basename(full_path))
        if name_norm in excluded_files_name:
            return "excluded_file"
        full_norm = norm_path(full_path)
        if full_norm in excluded_files_abs:
            return "excluded_file"
        rel_norm = norm_path(rel)
        if rel_norm in excluded_files_rel:
            return "excluded_file"
        try:
            rel_parts = Path(rel).parts
        except Exception:
            rel_parts = []
        blocked = False
        for idx in range(1, len(rel_parts)):
            prefix = os.path.normpath(os.path.join(*rel_parts[:idx]))
            prefix_norm = norm_path(prefix)
            if prefix_norm in excluded_dirs_rel_effective:
                blocked = True
                break
            try:
                abs_dir = os.path.join(root, prefix)
                abs_norm = norm_path(abs_dir)
                if excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs):
                    blocked = True
                    break
                if fp.is_virtual_env(abs_dir):
                    blocked = True
                    break
            except Exception:
                pass
        if blocked:
            return "excluded_dir"
        if max_depth and max_depth > 0:
            try:
                depth = 0 if len(rel_parts) <= 1 else len(rel_parts) - 1
            except Exception:
                depth = 0
            if depth > max_depth:
                return "depth_limit"
        try:
            size_bytes = os.path.getsize(full_path)
        except Exception:
            return "stat_error"
        if max_mb > 0 and (size_bytes / (1024 * 1024)) > max_mb:
            return "size_limit"
        try:
            is_binary = fp.is_binary_file(full_path)
        except Exception:
            is_binary = False
        if ignore_binary and is_binary:
            return "binary_ignored"
        if not include_all:
            ext = os.path.splitext(full_path)[1].lower().lstrip(".")
            if not file_types.get(ext, False):
                return f"ext_disabled:.{ext or 'noext'}"
        return None

    scanned_metas: List[FileMeta] = []

    def _add_meta(full_path: str, rel: str) -> None:
        try:
            stat = os.stat(full_path)
        except Exception:
            scan_skipped.append((rel, "stat_error"))
            return
        try:
            is_binary = fp.is_binary_file(full_path)
        except Exception:
            is_binary = False
        try:
            token_count = fp.file_token_count(full_path)
        except Exception:
            token_count = None
        category = fp.categorise(full_path, is_binary)
        meta = FileMeta(
            abs_path=Path(full_path),
            rel_path=Path(rel),
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            token_count=token_count,
            ext=os.path.splitext(full_path)[1].lower().lstrip("."),
            category=category,
            read_error=token_count is None,
        )
        scanned_metas.append(meta)

    if ui.scan.use_git_files:
        candidates = fp.git_files(root, include_untracked=ui.scan.git_include_untracked)
        for full_path in candidates:
            try:
                abs_path = str(Path(full_path).resolve())
            except Exception:
                abs_path = str(full_path)
            try:
                rel = os.path.relpath(abs_path, root)
            except Exception:
                rel = os.path.basename(abs_path)
            reason = _scan_reason(abs_path, rel)
            if reason:
                scan_skipped.append((rel, reason))
                continue
            _add_meta(abs_path, rel)
    else:
        visited: set[str] = set()
        for root_dir, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
            if max_depth and max_depth > 0:
                try:
                    rel_dir = os.path.relpath(root_dir, root)
                    depth = 0 if rel_dir in (".", "") else rel_dir.count(os.sep) + 1
                except Exception:
                    depth = 0
                if depth >= max_depth:
                    for d in list(dirnames):
                        try:
                            rel_pruned = os.path.relpath(os.path.join(root_dir, d), root)
                        except Exception:
                            rel_pruned = d
                        scan_skipped.append((rel_pruned, "depth_limit"))
                    dirnames[:] = []

            pruned: list[str] = []
            for d in list(dirnames):
                abs_dir = os.path.join(root_dir, d)
                try:
                    rel_dir_norm = norm_path(os.path.relpath(abs_dir, root))
                except Exception:
                    rel_dir_norm = norm_path(d)
                name_norm = norm_path(d)
                abs_norm = norm_path(abs_dir)
                try:
                    is_venv = fp.is_virtual_env(abs_dir)
                except Exception:
                    is_venv = False
                if (
                    name_norm in excluded_dirs_rel_effective
                    or rel_dir_norm in excluded_dirs_rel_effective
                    or (excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs))
                    or is_venv
                ):
                    pruned.append(d)
                    continue
                if follow_symlinks:
                    try:
                        real_path = os.path.realpath(abs_dir)
                    except Exception:
                        real_path = abs_dir
                    if real_path in visited:
                        pruned.append(d)
                        continue
                    visited.add(real_path)
            for d in pruned:
                try:
                    dirnames.remove(d)
                except ValueError:
                    pass

            for fname in filenames:
                full_path = os.path.join(root_dir, fname)
                rel = os.path.relpath(full_path, root)
                reason = _scan_reason(full_path, rel)
                if reason:
                    scan_skipped.append((rel, reason))
                    continue
                _add_meta(full_path, rel)

    dup_set = set()
    if ui.filters.hide_duplicates or ui.filters.mark_duplicates:
        try:
            dup_set = scan_service._compute_duplicate_set(scanned_metas, dup_bytes)  # type: ignore[attr-defined]
        except Exception:
            dup_set = set()

    git_changed = None
    if ui.scan.use_git_files and ui.scan.git_only_changed:
        since = str(ui.scan.git_since_ref or "").strip()
        if since:
            until = str(ui.scan.git_until_ref or "HEAD").strip() or "HEAD"
            git_changed = git_service.changed_set(root, since, until)

    vendored = None
    if ui.scan.exclude_vendored:
        try:
            rels = [meta.rel_path.as_posix() for meta in scanned_metas]
        except Exception:
            rels = []
        vendored = git_service.vendored_set(fp, root, rels) if rels else set()

    for meta in scanned_metas:
        keep = True
        why = ""
        rel_norm = meta.rel_path.as_posix()
        abs_norm = os.path.abspath(str(meta.abs_path))

        if rel_norm in custom_excluded and not ignore_custom:
            keep = False
            why = "custom_excluded"

        if keep and scan_service.always_include(meta, ui):
            keep = True
        else:
            if git_changed is not None and rel_norm not in git_changed:
                keep = False
                why = "git_not_changed"
            if keep and vendored is not None and rel_norm in vendored:
                keep = False
                why = "vendored"
            if keep and not ui.filters.category_mask.get(meta.category, True):
                keep = False
                why = "category"
            size_kb = meta.size_bytes / 1024.0
            if keep and not (ui.filters.size_min_kb <= size_kb <= ui.filters.size_max_kb):
                keep = False
                why = "size_filter"

            token_val = meta.token_count or 0
            if keep and ui.filters.apply_token_filters_to_transforms:
                try:
                    token_val = fp.processed_token_count(
                        str(meta.abs_path),
                        enumerate_lines=ui.filters.enumerate_lines,
                        remove_empty=ui.filters.remove_empty,
                    )
                except Exception:
                    token_val = meta.token_count or 0
            if keep and not (ui.filters.token_min <= token_val <= ui.filters.token_max):
                keep = False
                why = "token_filter"

            if keep and not _name_pattern_match(rel_norm, pattern, ui.filters.name_glob, compiled_pattern):
                keep = False
                why = "name_pattern"

            if keep and ui.filters.content_search:
                try:
                    ok = scan_service._content_match(  # type: ignore[attr-defined]
                        meta.abs_path,
                        ui.filters.content_search,
                        ui.filters.content_regex,
                        ui.filters.content_case,
                        content_bytes,
                    )
                except Exception:
                    ok = False
                if not ok:
                    keep = False
                    why = "content_search"

            if keep and ui.filters.date_after_ts and meta.mtime < ui.filters.date_after_ts:
                keep = False
                why = "date_after"
            if keep and ui.filters.date_before_ts and meta.mtime > ui.filters.date_before_ts:
                keep = False
                why = "date_before"

            if keep and ui.filters.hide_duplicates and abs_norm in dup_set:
                keep = False
                why = "duplicate_hidden"

        if keep:
            included.append(rel_norm)
        else:
            filtered_out.append((rel_norm, why or "filtered"))

    lines: List[str] = []
    lines.append(f"Directory: {root}")
    lines.append(f"Visibility: {'show_all' if show_all else 'filters'}")
    if show_all:
        lines.append("Note: filters/excludes are ignored in show-all mode.")
    elif ignore_custom:
        lines.append("Note: custom excludes are ignored for this run.")
    lines.append(
        "Scan settings: include_all={include_all} ignore_binary={ignore_binary} max_mb={max_mb} "
        "max_depth={max_depth} follow_symlinks={follow_symlinks} use_git_files={use_git_files} "
        "(untracked={git_untracked} changed_only={git_changed} exclude_vendored={exclude_vendored} "
        "since={git_since} until={git_until})".format(
            include_all=include_all,
            ignore_binary=ignore_binary,
            max_mb=max_mb,
            max_depth=ui.scan.max_depth,
            follow_symlinks=ui.scan.follow_symlinks,
            use_git_files=ui.scan.use_git_files,
            git_untracked=ui.scan.git_include_untracked,
            git_changed=ui.scan.git_only_changed,
            exclude_vendored=ui.scan.exclude_vendored,
            git_since=ui.scan.git_since_ref or "(none)",
            git_until=ui.scan.git_until_ref or "HEAD",
        )
    )
    if ui.scan.use_git_files:
        lines.append("Note: git-only mode limits scan candidates to git-tracked/untracked files.")
        if not scanned_metas:
            lines.append("Note: no git files discovered (repo missing or empty).")
    excluded_dirs_count = len(excluded_dirs_rel) + len(excluded_dirs_abs)
    excluded_files_count = len(excluded_files_name) + len(excluded_files_rel) + len(excluded_files_abs)
    lines.append(
        "Excludes: dirs={dirs} files={files} custom={custom} "
        "noise_flags(node={node} ios={ios} py={py} java={java} web={web})".format(
            dirs=excluded_dirs_count,
            files=excluded_files_count,
            custom=len(custom_excluded),
            node=node_noise,
            ios=ios_noise,
            py=python_noise,
            java=java_noise,
            web=web_noise,
        )
    )
    read_errors = sum(1 for meta in scanned_metas if meta.read_error)
    lines.append(f"Read errors: {read_errors}")
    if include_all:
        lines.append("File types: include_all")
    else:
        total_ext = len(file_types)
        enabled_ext = sum(1 for v in file_types.values() if v)
        lines.append(f"File types enabled: {enabled_ext}/{total_ext}")

    cat_total = len(ui.filters.category_mask)
    cat_enabled = sum(1 for v in ui.filters.category_mask.values() if v)
    lines.append(f"Categories enabled: {cat_enabled}/{cat_total}")

    def _fmt_ts(ts: float) -> str:
        if not ts:
            return "(none)"
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return str(ts)

    name_info = pattern if pattern else "(none)"
    content_info = ui.filters.content_search if ui.filters.content_search else "(none)"
    if ui.filters.content_search:
        flags = []
        if ui.filters.content_regex:
            flags.append("regex")
        if ui.filters.content_case:
            flags.append("case")
        if flags:
            content_info = f"{content_info} ({', '.join(flags)})"
    lines.append(
        "Filters: tokens={tok_min}-{tok_max} size_kb={sz_min}-{sz_max} "
        "name_pattern={name} content={content} date_after={after} date_before={before}".format(
            tok_min=ui.filters.token_min,
            tok_max=ui.filters.token_max,
            sz_min=ui.filters.size_min_kb,
            sz_max=ui.filters.size_max_kb,
            name=name_info,
            content=content_info,
            after=_fmt_ts(ui.filters.date_after_ts),
            before=_fmt_ts(ui.filters.date_before_ts),
        )
    )
    lines.append(
        "Always-include docs: {enabled} (list={count})".format(
            enabled=ui.filters.always_include_docs,
            count=len(ui.filters.always_include_list or []),
        )
    )
    lines.append(
        "Visible now: {visible_count} | Included post-filters: {included_ct} | "
        "Skipped at scan: {skipped_ct} | Filtered out: {filtered_ct}".format(
            visible_count=len(visible_rel),
            included_ct=len(included),
            skipped_ct=len(scan_skipped),
            filtered_ct=len(filtered_out),
        )
    )
    lines.append("")

    def _summarise(items: List[Tuple[str, str]], label: str) -> None:
        counts: Dict[str, int] = {}
        for _rel, reason in items:
            counts[reason] = counts.get(reason, 0) + 1
        if not counts:
            return
        parts = [f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
        lines.append(f"{label}: " + ", ".join(parts))
        lines.append("")

    _summarise(scan_skipped, "Skipped reasons")
    _summarise(filtered_out, "Filtered reasons")

    all_candidates = included + [rel for rel, _ in filtered_out] + [rel for rel, _ in scan_skipped]
    missing_readmes = [p for p in all_candidates if p.lower().endswith("readme.md")]
    if missing_readmes and not any(p.lower().endswith("readme.md") for p in visible_rel):
        lines.append("Potential issue: README.md not visible. See reasons below.")
        lines.append("")

    lines.append("-- First 20 skipped-at-scan files (reason) --")
    for rel, why in scan_skipped[:20]:
        lines.append(f"SCAN  ✖  {rel}    [{why}]")
    lines.append("")

    lines.append("-- First 20 filtered-out files (reason) --")
    for rel, why in filtered_out[:20]:
        lines.append(f"FILTER ✖  {rel}    [{why}]")
    lines.append("")

    lines.append("-- First 20 visible files --")
    for rel in list(visible_rel)[:20]:
        lines.append(f"OK     ✓  {rel}")

    return "\n".join(lines)
