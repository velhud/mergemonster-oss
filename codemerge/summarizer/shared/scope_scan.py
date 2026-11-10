from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from codemerge.filemerger.exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)
from codemerge.filemerger.scan import scan_directory
from codemerge.filemerger.services import scan_service
from codemerge.filemerger.types import FileProcessorLike as FileProcessor
from codemerge.models import FileMeta, ScanContext

_AGGREGATE_NOISE_FLAG_DEFAULTS: dict[str, bool] = {
    "exclude_python_noise": True,
    "exclude_web_build_noise": True,
    "exclude_java_gradle_noise": True,
}
_AGGREGATE_NOISE_DIRS = {
    ".cache",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
    ".hypothesis",
    ".pytype",
    "log",
    "logs",
}


def _is_ancestor_or_same(path: str, target: str) -> bool:
    try:
        path_obj = Path(path).resolve()
        target_obj = Path(target).resolve()
    except Exception:
        return False
    if path_obj == target_obj:
        return True
    try:
        return target_obj.is_relative_to(path_obj)
    except AttributeError:
        try:
            target_obj.relative_to(path_obj)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _normalize_exclusion_entry(entry: object) -> str:
    if entry is None:
        return ""
    try:
        raw = str(entry).strip()
    except Exception:
        return ""
    if not raw:
        return ""
    expanded = raw
    try:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if os.path.isabs(expanded):
            return os.path.normcase(os.path.abspath(expanded))
    except Exception:
        expanded = raw
    try:
        return os.path.normcase(os.path.normpath(expanded))
    except Exception:
        return raw


def _norm_str_set(items: object) -> set[str]:
    if not isinstance(items, (list, set, tuple)):
        return set()
    out: set[str] = set()
    for item in items:
        norm = _normalize_exclusion_entry(item)
        if norm:
            out.add(norm)
    return out


def compute_aggregate_excluded_dirs(
    app_config: Mapping[str, Any] | None,
    *,
    file_processor: FileProcessor,
    config_overrides: Mapping[str, Any] | None = None,
) -> set[str]:
    merged_cfg = dict(app_config or {})
    if isinstance(config_overrides, Mapping):
        merged_cfg.update(config_overrides)
    for key, value in _AGGREGATE_NOISE_FLAG_DEFAULTS.items():
        merged_cfg.setdefault(key, value)

    raw_excluded = merged_cfg.get("excluded_dirs") if "excluded_dirs" in merged_cfg else None
    if raw_excluded is None or not isinstance(raw_excluded, (list, set, tuple)):
        try:
            base_excluded_dirs = set(getattr(file_processor, "default_excluded_dirs", set()))
        except Exception:
            base_excluded_dirs = set()
    else:
        base_excluded_dirs = _norm_str_set(raw_excluded)
    excluded = scan_service.compute_noise_exclusions(base_excluded_dirs, merged_cfg)
    excluded |= {os.path.normpath(item) for item in _AGGREGATE_NOISE_DIRS}
    return excluded


def compute_aggregate_excluded_files(
    app_config: Mapping[str, Any] | None,
    *,
    config_overrides: Mapping[str, Any] | None = None,
) -> set[str]:
    merged_cfg = dict(app_config or {})
    if isinstance(config_overrides, Mapping):
        merged_cfg.update(config_overrides)
    return {item for item in _norm_str_set(merged_cfg.get("excluded_files", [])) if item}


def compute_aggregate_custom_exclusions(
    app_config: Mapping[str, Any] | None,
    *,
    root_abs: str,
    config_overrides: Mapping[str, Any] | None = None,
) -> tuple[set[str], set[str]]:
    merged_cfg = dict(app_config or {})
    if isinstance(config_overrides, Mapping):
        merged_cfg.update(config_overrides)
    raw_items = merged_cfg.get("custom_excluded", [])
    if not isinstance(raw_items, (list, set, tuple)):
        return set(), set()

    root = Path(root_abs)
    dirs: set[str] = set()
    files: set[str] = set()
    for entry in raw_items:
        norm = _normalize_exclusion_entry(entry)
        if not norm:
            continue
        raw = str(entry).strip()
        candidate: Path | None = None
        try:
            candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
            if not candidate.is_absolute():
                candidate = root / candidate
            candidate = candidate.resolve()
        except Exception:
            candidate = None

        if candidate is not None:
            try:
                if candidate.is_dir():
                    dirs.add(norm)
                    continue
                if candidate.is_file():
                    files.add(norm)
                    continue
            except Exception:
                pass

        trimmed = raw.rstrip("/\\")
        basename = os.path.basename(trimmed)
        has_separator = bool(os.sep in raw or (os.altsep and os.altsep in raw))
        if raw.endswith((os.sep, "/")):
            dirs.add(norm)
        elif has_separator and "." not in basename:
            dirs.add(norm)
        elif not has_separator and "." not in basename:
            dirs.add(norm)
        else:
            files.add(norm)
    return dirs, files


def _allow_explicit_root_within_exclusions(excluded_dirs: Iterable[str], *, root_abs: str) -> set[str]:
    root_norm = _normalize_exclusion_entry(root_abs)
    if not root_norm:
        return {item for item in excluded_dirs if item}
    filtered: set[str] = set()
    for item in excluded_dirs:
        norm = _normalize_exclusion_entry(item)
        if not norm:
            continue
        if os.path.isabs(norm) and _is_ancestor_or_same(norm, root_norm):
            continue
        filtered.add(norm)
    return filtered


def _prune_exclusions_for_root(
    *,
    root_abs: str,
    excluded_dirs: Iterable[str],
    excluded_files: Iterable[str],
) -> tuple[set[str], set[str]]:
    root_norm = os.path.normcase(os.path.abspath(root_abs))
    scoped_dirs: set[str] = set()
    for item in excluded_dirs:
        norm = _normalize_exclusion_entry(item)
        if not norm:
            continue
        if os.path.isabs(norm) and _is_ancestor_or_same(norm, root_norm):
            continue
        scoped_dirs.add(norm)
    scoped_files: set[str] = set()
    for item in excluded_files:
        norm = _normalize_exclusion_entry(item)
        if not norm:
            continue
        if os.path.isabs(norm) and _is_ancestor_or_same(norm, root_norm):
            continue
        scoped_files.add(norm)
    return scoped_dirs, scoped_files


def filter_aggregate_scan_files(
    scan_files: Sequence[FileMeta],
    *,
    excluded_dirs: Iterable[str],
    excluded_files: Iterable[str],
) -> list[FileMeta]:
    excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(excluded_dirs)
    excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(excluded_files)
    files: list[FileMeta] = []
    for meta in scan_files:
        rel = norm_path(meta.rel_path.as_posix())
        abs_norm = norm_path(str(meta.abs_path))
        name_norm = norm_path(meta.rel_path.name)
        if name_norm in excluded_files_name:
            continue
        if rel in excluded_files_rel or abs_norm in excluded_files_abs:
            continue
        if excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs):
            continue
        if any(part in excluded_dirs_rel for part in rel.split(os.sep)):
            continue
        files.append(meta)
    return files


class AggregateScanHost:
    """Per-run scan host that applies exclusion overlays without mutating the processor."""

    def __init__(self, file_processor: FileProcessor, *, excluded_dirs: set[str], excluded_files: set[str]) -> None:
        self._fp = file_processor
        self.excluded_dirs = excluded_dirs
        self.excluded_files = excluded_files

    def is_virtual_env(self, dir_path: str) -> bool:
        return self._fp.is_virtual_env(dir_path)

    def is_binary_file(self, file_path: str) -> bool:
        return self._fp.is_binary_file(file_path)

    def categorise(self, path: str, is_binary_hint: bool = False):
        return self._fp.categorise(path, is_binary_hint=is_binary_hint)

    def file_token_count(self, path: str) -> int | None:
        return self._fp.file_token_count(path)


def scan_aggregate_scope_files(
    *,
    root_abs: str,
    file_processor: FileProcessor,
    app_config: Mapping[str, Any] | None,
    config_overrides: Mapping[str, Any] | None = None,
    base_scan_ctx: ScanContext | None = None,
    allow_scan_ctx_reuse: bool = True,
) -> list[FileMeta]:
    merged_cfg = dict(app_config or {})
    if isinstance(config_overrides, Mapping):
        merged_cfg.update(config_overrides)

    excluded_dirs = compute_aggregate_excluded_dirs(
        merged_cfg,
        file_processor=file_processor,
    )
    excluded_files = compute_aggregate_excluded_files(merged_cfg)
    custom_excluded_dirs, custom_excluded_files = compute_aggregate_custom_exclusions(
        merged_cfg,
        root_abs=root_abs,
    )
    excluded_dirs = set(excluded_dirs) | set(custom_excluded_dirs)
    excluded_files = set(excluded_files) | set(custom_excluded_files)
    excluded_dirs = _allow_explicit_root_within_exclusions(excluded_dirs, root_abs=root_abs)
    excluded_dirs, excluded_files = _prune_exclusions_for_root(
        root_abs=root_abs,
        excluded_dirs=excluded_dirs,
        excluded_files=excluded_files,
    )

    use_existing = False
    if allow_scan_ctx_reuse and base_scan_ctx and getattr(base_scan_ctx, "files", None):
        try:
            use_existing = Path(base_scan_ctx.root).resolve() == Path(root_abs).resolve()
        except Exception:
            use_existing = False

    if use_existing:
        files = filter_aggregate_scan_files(
            list(base_scan_ctx.files),
            excluded_dirs=excluded_dirs,
            excluded_files=excluded_files,
        )
    else:
        host = AggregateScanHost(
            file_processor,
            excluded_dirs=excluded_dirs,
            excluded_files=excluded_files,
        )
        scan_ctx = scan_directory(
            host,
            root_abs,
            file_types={},
            include_all=True,
            sort_by="name",
            sort_descending=False,
            ignore_binary=True,
            max_file_size_mb=0.0,
            token_workers=int(merged_cfg.get("token_workers", 0) or 0),
            max_depth=int(merged_cfg.get("max_walk_depth", 0) or 0),
            follow_symlinks=bool(merged_cfg.get("follow_symlinks", False)),
        )
        files = list(scan_ctx.files)

    if bool(merged_cfg.get("exclude_vendored", False)):
        rels = [meta.rel_path.as_posix() for meta in files]
        try:
            vendored = set(file_processor.git_vendored_set(root_abs, rels))
        except Exception:
            vendored = set()
        if vendored:
            files = [meta for meta in files if meta.rel_path.as_posix() not in vendored]
    return files


__all__ = [
    "AggregateScanHost",
    "compute_aggregate_custom_exclusions",
    "compute_aggregate_excluded_dirs",
    "compute_aggregate_excluded_files",
    "filter_aggregate_scan_files",
    "scan_aggregate_scope_files",
]
