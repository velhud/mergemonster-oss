from __future__ import annotations

import contextlib
import fnmatch
import os
import platform
import stat
from pathlib import Path
from typing import Callable, Dict, List, Protocol, Sequence

from codemerge.models import Category, FileMeta, ScanContext
from .exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)


class ScanHost(Protocol):
    excluded_dirs: set[str]
    excluded_files: set[str]

    def is_virtual_env(self, dir_path: str) -> bool: ...

    def is_binary_file(self, file_path: str) -> bool: ...

    def categorise(self, path: str, is_binary_hint: bool = False) -> Category: ...

    def file_token_count(self, path: str) -> int | None: ...


class ScanError(RuntimeError):
    """Raised when directory traversal fails."""


def _walk_directory_allowed(path: str, root: str, follow_links: bool, visited: set[str]) -> bool:
    if not follow_links and os.path.islink(path):
        return False
    real = os.path.realpath(path)
    if real in visited or not Path(real).is_relative_to(root):
        return False
    if follow_links:
        visited.add(real)
    return True


def _regular_file_in_root(path: str, root: str, follow_links: bool) -> bool:
    try:
        if not follow_links and os.path.islink(path):
            return False
        return (
            Path(os.path.realpath(path)).is_relative_to(root)
            and stat.S_ISREG(os.stat(path).st_mode)
        )
    except OSError:
        return False


def find_always_include_files(
    host: ScanHost,
    directory: str,
    patterns: Sequence[str],
    *,
    ignore_binary: bool = False,
    max_file_size_mb: float = 0.0,
    max_depth: int = 0,
    follow_symlinks: bool = False,
) -> List[str]:
    """Return absolute paths for files matching always-include patterns."""

    if not patterns:
        return []

    patterns_norm = [str(p).lower() for p in patterns if p]
    if not patterns_norm:
        return []

    excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(host.excluded_dirs)
    excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(host.excluded_files)
    matched: list[str] = []
    canonical_root = os.path.realpath(directory)
    visited: set[str] = {canonical_root}

    def _is_match(name: str, stem: str) -> bool:
        name_l = name.lower()
        stem_l = stem.lower()
        for pat in patterns_norm:
            if any(ch in pat for ch in "*?["):
                try:
                    if fnmatch.fnmatchcase(name_l, pat):
                        return True
                except Exception:
                    continue
            else:
                if stem_l.startswith(pat):
                    return True
        return False

    for root_dir, dirs, files in os.walk(directory, followlinks=follow_symlinks):
        dirs_to_remove: list[str] = []
        for d in list(dirs):
            dir_path = os.path.join(root_dir, d)
            if not _walk_directory_allowed(dir_path, canonical_root, follow_symlinks, visited):
                dirs_to_remove.append(d)
                continue
            rel_dir = norm_path(os.path.relpath(dir_path, directory))
            dir_norm = norm_path(dir_path)
            name_norm = norm_path(d)
            try:
                is_venv = host.is_virtual_env(dir_path)
            except Exception:
                is_venv = False
            if (
                name_norm in excluded_dirs_rel
                or rel_dir in excluded_dirs_rel
                or (excluded_dirs_abs and is_abs_excluded(dir_norm, excluded_dirs_abs))
                or is_venv
            ):
                dirs_to_remove.append(d)

        if max_depth and max_depth > 0:
            try:
                rel = os.path.relpath(root_dir, directory)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
            except Exception:
                depth = 0
            if depth >= max_depth:
                dirs[:] = []
        for d in dirs_to_remove:
            if d in dirs:
                dirs.remove(d)

        for file_name in files:
            name_norm = norm_path(file_name)
            if name_norm in excluded_files_name:
                continue
            full_path = os.path.join(root_dir, file_name)
            if not _regular_file_in_root(full_path, canonical_root, follow_symlinks):
                continue
            full_norm = norm_path(full_path)
            if full_norm in excluded_files_abs:
                continue
            rel_file = norm_path(os.path.relpath(full_path, directory))
            if rel_file in excluded_files_rel:
                continue
            if ignore_binary:
                try:
                    is_binary = host.is_binary_file(full_path)
                except Exception:
                    is_binary = False
                if is_binary:
                    continue
            if max_file_size_mb > 0:
                try:
                    size_mb = os.path.getsize(full_path) / (1024 * 1024)
                except Exception:
                    size_mb = None
                if size_mb is not None and size_mb > max_file_size_mb:
                    continue
            try:
                stem = Path(file_name).stem
            except Exception:
                stem = os.path.splitext(file_name)[0]
            if _is_match(file_name, stem):
                matched.append(full_path)

    return matched


def discover_extensions(
    host: ScanHost,
    directory: str,
    *,
    max_depth: int = 0,
    follow_symlinks: bool = False,
) -> set[str]:
    """Walk ``directory`` and collect file suffixes regardless of active filters.

    The traversal mirrors :func:`gather_files` rules: directory excludes,
    virtual-environment detection, optional depth limiting, and optional
    symlink following. Only metadata is inspected; file contents are never read.
    """

    discovered: set[str] = set()
    directory = os.path.abspath(directory)

    if not os.path.isdir(directory):
        return discovered

    canonical_root = os.path.realpath(directory)
    visited: set[str] = {canonical_root}
    excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(host.excluded_dirs)
    excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(host.excluded_files)

    try:
        walker = os.walk(directory, followlinks=follow_symlinks)
    except (PermissionError, OSError):
        return discovered

    for root_dir, dirs, files in walker:
        dirs_to_remove: list[str] = []
        for entry in list(dirs):
            dir_path = os.path.join(root_dir, entry)

            if not _walk_directory_allowed(dir_path, canonical_root, follow_symlinks, visited):
                dirs_to_remove.append(entry)
                continue

            rel_dir = norm_path(os.path.relpath(dir_path, directory))
            entry_norm = norm_path(entry)
            dir_norm = norm_path(dir_path)
            try:
                is_venv = host.is_virtual_env(dir_path)
            except Exception:
                is_venv = False
            if (
                entry_norm in excluded_dirs_rel
                or rel_dir in excluded_dirs_rel
                or (excluded_dirs_abs and is_abs_excluded(dir_norm, excluded_dirs_abs))
                or is_venv
            ):
                dirs_to_remove.append(entry)

        if max_depth and max_depth > 0:
            try:
                rel = os.path.relpath(root_dir, directory)
            except Exception:
                rel = "."
            depth = 0 if rel in (".", "") else rel.count(os.sep) + 1
            if depth >= max_depth:
                dirs[:] = []

        for blocked in dirs_to_remove:
            with contextlib.suppress(ValueError):
                dirs.remove(blocked)

        for name in files:
            name_norm = norm_path(name)
            if name_norm in excluded_files_name:
                continue
            full_path = os.path.join(root_dir, name)
            if not _regular_file_in_root(full_path, canonical_root, follow_symlinks):
                continue
            full_norm = norm_path(full_path)
            if full_norm in excluded_files_abs:
                continue
            rel_file = norm_path(os.path.relpath(full_path, directory))
            if rel_file in excluded_files_rel:
                continue

            try:
                if host.is_binary_file(full_path):
                    continue
            except Exception:
                pass

            try:
                ext = Path(full_path).suffix.lower().lstrip(".")
            except Exception:
                ext = ""
            discovered.add(ext)

    return discovered


def gather_files(
    host: ScanHost,
    directory: str,
    file_types: Dict[str, bool],
    include_all: bool,
    *,
    sort_by: str = "name",
    sort_descending: bool = False,
    ignore_binary: bool = False,
    max_file_size_mb: float = 0.0,
    max_depth: int = 0,
    follow_symlinks: bool = False,
) -> List[str]:
    """Return a filtered file list rooted at ``directory``."""

    collected_files: list[str] = []
    excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(host.excluded_dirs)
    excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(host.excluded_files)

    try:
        canonical_root = os.path.realpath(directory)
        visited: set[str] = {canonical_root}
        for root_dir, dirs, files in os.walk(directory, followlinks=follow_symlinks):
            dirs_to_remove: list[str] = []
            for d in list(dirs):
                dir_path = os.path.join(root_dir, d)
                if not _walk_directory_allowed(dir_path, canonical_root, follow_symlinks, visited):
                    dirs_to_remove.append(d)
                    continue
                rel_dir = norm_path(os.path.relpath(dir_path, directory))
                dir_norm = norm_path(dir_path)
                name_norm = norm_path(d)
                try:
                    is_venv = host.is_virtual_env(dir_path)
                except Exception:
                    is_venv = False
                if (
                    name_norm in excluded_dirs_rel
                    or rel_dir in excluded_dirs_rel
                    or (excluded_dirs_abs and is_abs_excluded(dir_norm, excluded_dirs_abs))
                    or is_venv
                ):
                    dirs_to_remove.append(d)

            if max_depth and max_depth > 0:
                try:
                    rel = os.path.relpath(root_dir, directory)
                    depth = 0 if rel == "." else rel.count(os.sep) + 1
                except Exception:
                    depth = 0
                if depth >= max_depth:
                    dirs[:] = []
            for d in dirs_to_remove:
                if d in dirs:
                    dirs.remove(d)

            for file_name in files:
                name_norm = norm_path(file_name)
                if name_norm in excluded_files_name:
                    continue
                full_path = os.path.join(root_dir, file_name)
                if not _regular_file_in_root(full_path, canonical_root, follow_symlinks):
                    continue
                full_norm = norm_path(full_path)
                if full_norm in excluded_files_abs:
                    continue
                rel_file = norm_path(os.path.relpath(full_path, directory))
                if rel_file in excluded_files_rel:
                    continue
                if ignore_binary:
                    try:
                        is_binary = host.is_binary_file(full_path)
                    except Exception:
                        is_binary = False
                    if is_binary:
                        continue
                if max_file_size_mb > 0:
                    try:
                        size_mb = os.path.getsize(full_path) / (1024 * 1024)
                    except Exception:
                        size_mb = None
                    if size_mb is not None and size_mb > max_file_size_mb:
                        continue

                if include_all:
                    collected_files.append(full_path)
                else:
                    file_ext = os.path.splitext(file_name)[1].lower()
                    if file_ext.startswith("."):
                        file_ext = file_ext[1:]
                    else:
                        file_ext = ""
                    for ext, include_flag in file_types.items():
                        if include_flag and file_ext == ext:
                            collected_files.append(full_path)
                            break
    except (PermissionError, OSError) as exc:
        raise ScanError(f"Error accessing directory: {exc}") from exc

    if sort_by not in ("name", "size", "date"):
        sort_by = "name"

    def _safe_stat(path: str, getter: Callable[[str], float], fallback: float) -> float:
        try:
            return getter(path)
        except Exception:
            return fallback

    if sort_by == "name":
        collected_files.sort(key=lambda path: path.lower(), reverse=sort_descending)
    elif sort_by == "size":
        fallback = -1 if sort_descending else float("inf")
        collected_files.sort(
            key=lambda path: _safe_stat(path, os.path.getsize, fallback),
            reverse=sort_descending,
        )
    elif sort_by == "date":
        fallback = -1.0 if sort_descending else float("inf")
        collected_files.sort(
            key=lambda path: _safe_stat(path, os.path.getmtime, fallback),
            reverse=sort_descending,
        )

    return collected_files


def scan_directory(
    host: ScanHost,
    directory: str,
    file_types: Dict[str, bool],
    include_all: bool,
    *,
    sort_by: str = "name",
    sort_descending: bool = False,
    ignore_binary: bool = False,
    max_file_size_mb: float = 0.0,
    token_workers: int = 0,
    max_depth: int = 0,
    follow_symlinks: bool = False,
    progress_callback: Callable[[float, str], None] | None = None,
) -> ScanContext:
    """Return a :class:`ScanContext` describing ``directory``."""

    plain_paths = gather_files(
        host,
        directory,
        file_types,
        include_all,
        sort_by=sort_by,
        sort_descending=sort_descending,
        ignore_binary=ignore_binary,
        max_file_size_mb=max_file_size_mb,
        max_depth=max_depth,
        follow_symlinks=follow_symlinks,
    )

    base_path = Path(os.path.abspath(directory))
    files_meta: list[FileMeta] = []
    by_ext: dict[str, list[int]] = {}
    by_category: dict[Category, list[int]] = {}

    tokens: dict[str, int | None] = {}
    total_files = len(plain_paths)
    progress_stride = max(1, total_files // 50) if total_files else 1

    def _emit(done: int, base: float, span: float, label: str) -> None:
        if not progress_callback or not total_files:
            return
        if done % progress_stride and done != total_files:
            return
        pct = base + (done / total_files) * span
        progress_callback(pct, f"{label} {done}/{total_files}")

    if plain_paths:
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            workers = (
                token_workers
                if token_workers and token_workers > 0
                else (os.cpu_count() or 4)
            )
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(host.file_token_count, fp): fp for fp in plain_paths}
                done = 0
                for future in as_completed(futures):
                    path = futures[future]
                    try:
                        tokens[path] = future.result()
                    except Exception:
                        tokens[path] = None
                    done += 1
                    _emit(done, 0.0, 60.0, "Tokenizing")
        except Exception:
            done = 0
            for fp in plain_paths:
                try:
                    tokens[fp] = host.file_token_count(fp)
                except Exception:
                    tokens[fp] = None
                done += 1
                _emit(done, 0.0, 60.0, "Tokenizing")

    for index, file_path in enumerate(plain_paths):
        try:
            abs_path = Path(os.path.abspath(file_path))
        except Exception:
            abs_path = Path(file_path)

        stat = None
        try:
            stat = abs_path.stat()
        except Exception:
            try:
                stat = Path(file_path).stat()
            except Exception:
                stat = None

        ext = abs_path.suffix.lower().lstrip(".")
        try:
            is_binary = host.is_binary_file(file_path)
        except Exception:
            is_binary = False
        try:
            category = host.categorise(file_path, is_binary)
        except Exception:
            category = Category.BINARY if is_binary else Category.BACKEND
        if tokens:
            token_count = tokens.get(file_path)
        else:
            try:
                token_count = host.file_token_count(file_path)
            except Exception:
                token_count = None

        try:
            rel_path = abs_path.relative_to(base_path)
        except ValueError:
            rel_path = Path(os.path.relpath(abs_path, base_path))

        size_bytes = stat.st_size if stat else 0
        mtime = stat.st_mtime if stat else 0.0
        meta = FileMeta(
            abs_path=abs_path,
            rel_path=rel_path,
            size_bytes=size_bytes,
            mtime=mtime,
            token_count=token_count,
            ext=ext,
            category=category,
            read_error=token_count is None or stat is None,
        )
        files_meta.append(meta)
        by_ext.setdefault(ext, []).append(index)
        by_category.setdefault(category, []).append(index)
        _emit(index + 1, 60.0, 40.0, "Indexing")

    aggregate_tokens = sum(meta.token_count or 0 for meta in files_meta)
    aggregate_bytes = sum(meta.size_bytes for meta in files_meta)

    return ScanContext(
        root=base_path,
        files=files_meta,
        aggregate_tokens=aggregate_tokens,
        aggregate_bytes=aggregate_bytes,
        by_ext=by_ext,
        by_category=by_category,
    )


def scan_file_list(
    host: ScanHost,
    root: str,
    files: Sequence[str],
    *,
    sort_by: str = "name",
    sort_descending: bool = False,
    progress_callback: Callable[[float, str], None] | None = None,
) -> ScanContext:
    """Build a :class:`ScanContext` for an explicit list of ``files``."""

    base_path = Path(os.path.abspath(root))
    normalised: list[Path] = []
    for entry in files:
        try:
            normalised.append(Path(os.path.abspath(entry)))
        except Exception:
            continue

    def _key(path: Path):
        try:
            if sort_by == "size":
                return path.stat().st_size
            if sort_by == "date":
                return path.stat().st_mtime
            return str(path).lower()
        except Exception:
            return str(path).lower()

    normalised.sort(key=_key, reverse=bool(sort_descending))

    files_meta: list[FileMeta] = []
    by_ext: dict[str, list[int]] = {}
    by_category: dict[Category, list[int]] = {}

    total_tokens = 0
    total_bytes = 0

    total_files = len(normalised)
    progress_stride = max(1, total_files // 50) if total_files else 1

    def _emit(done: int) -> None:
        if not progress_callback or not total_files:
            return
        if done % progress_stride and done != total_files:
            return
        pct = (done / total_files) * 100.0
        progress_callback(pct, f"Scanning {done}/{total_files}")

    for index, path in enumerate(normalised):
        stat = None
        try:
            stat = path.stat()
        except Exception:
            stat = None
        ext = path.suffix.lower().lstrip(".")
        try:
            is_binary = host.is_binary_file(str(path))
        except Exception:
            is_binary = False
        try:
            category = host.categorise(str(path), is_binary)
        except Exception:
            category = Category.BINARY if is_binary else Category.BACKEND
        try:
            token_count = host.file_token_count(str(path))
        except Exception:
            token_count = None
        try:
            rel_path = path.relative_to(base_path)
        except Exception:
            try:
                rel_path = Path(os.path.relpath(path, base_path))
            except Exception:
                rel_path = Path(os.path.basename(str(path)))

        size_bytes = stat.st_size if stat else 0
        mtime = stat.st_mtime if stat else 0.0
        meta = FileMeta(
            abs_path=path,
            rel_path=rel_path,
            size_bytes=size_bytes,
            mtime=mtime,
            token_count=token_count,
            ext=ext,
            category=category,
            read_error=token_count is None or stat is None,
        )
        files_meta.append(meta)
        by_ext.setdefault(ext, []).append(index)
        by_category.setdefault(category, []).append(index)

        total_tokens += token_count or 0
        total_bytes += size_bytes
        _emit(index + 1)

    return ScanContext(
        root=base_path,
        files=files_meta,
        aggregate_tokens=total_tokens,
        aggregate_bytes=total_bytes,
        by_ext=by_ext,
        by_category=by_category,
    )


def append_files_to_context(
    ctx: ScanContext | None,
    host: ScanHost,
    root: str,
    files: Sequence[str],
) -> tuple[ScanContext, List[FileMeta]]:
    """Append arbitrary files into an existing scan ctx without losing state."""

    requested_root = Path(os.path.abspath(root)) if root else None
    if ctx is None:
        base_root = requested_root or Path.cwd()
        ctx = ScanContext(
            root=base_root,
            files=[],
            aggregate_tokens=0,
            aggregate_bytes=0,
            by_ext={},
            by_category={},
        )
    else:
        if not ctx.files and requested_root is not None:
            ctx.root = requested_root
        base_root = ctx.root
        if requested_root is None:
            requested_root = base_root

    base_root = ctx.root if ctx.files else requested_root or ctx.root

    existing: Dict[str, int] = {}
    for index, meta in enumerate(ctx.files):
        try:
            key = str(Path(meta.abs_path).resolve())
        except Exception:
            key = os.path.abspath(str(meta.abs_path))
        existing[key] = index

    added: List[FileMeta] = []
    for entry in files:
        if not entry:
            continue
        try:
            entry_path = Path(str(entry))
            if not entry_path.is_absolute() and requested_root is not None:
                abs_path = Path(os.path.abspath(os.path.join(str(requested_root), str(entry))))
            else:
                abs_path = Path(os.path.abspath(str(entry)))
        except Exception:
            continue
        if not abs_path.exists() or not abs_path.is_file():
            continue
        try:
            canonical = str(abs_path.resolve())
        except Exception:
            canonical = str(abs_path)
        if canonical in existing:
            continue
        stat = None
        try:
            stat = abs_path.stat()
        except Exception:
            stat = None

        ext = abs_path.suffix.lower().lstrip(".")
        try:
            is_binary = host.is_binary_file(str(abs_path))
        except Exception:
            is_binary = False
        try:
            category = host.categorise(str(abs_path), is_binary)
        except Exception:
            category = Category.BINARY if is_binary else Category.BACKEND
        try:
            token_count = host.file_token_count(str(abs_path))
        except Exception:
            token_count = None

        base_for_rel = base_root or abs_path.parent
        try:
            rel_path = abs_path.relative_to(base_for_rel)
        except Exception:
            try:
                rel_path = Path(os.path.relpath(abs_path, base_for_rel))
            except Exception:
                rel_path = Path(abs_path.name)

        size_bytes = stat.st_size if stat else 0
        mtime = stat.st_mtime if stat else 0.0
        meta = FileMeta(
            abs_path=abs_path,
            rel_path=rel_path,
            size_bytes=size_bytes,
            mtime=mtime,
            token_count=token_count,
            ext=ext,
            category=category,
            read_error=token_count is None or stat is None,
        )

        ctx.files.append(meta)
        index = len(ctx.files) - 1
        ctx.by_ext.setdefault(ext, []).append(index)
        ctx.by_category.setdefault(category, []).append(index)
        ctx.aggregate_tokens += token_count or 0
        ctx.aggregate_bytes += size_bytes
        added.append(meta)
        existing[canonical] = index

    return ctx, added


def generate_file_tree(
    directory: str,
    files: Sequence[str],
    ascii_mode: bool | None = None,
    summarized_folders: Sequence[str] | dict[str, str] | None = None,
    pinned_files: Sequence[str] | None = None,
) -> str:
    if not files and not summarized_folders:
        return "No files selected."

    base_dir = os.path.basename(directory) or directory
    use_ascii = platform.system() == "Windows" if ascii_mode is None else ascii_mode
    vertical = "|   " if use_ascii else "│   "
    tee = "+-- " if use_ascii else "├── "
    last = "\\-- " if use_ascii else "└── "

    tree = f"File Tree for '{base_dir}':\n"


    def _to_rel(raw_path: str) -> str:
        if os.path.isabs(raw_path):
            try:
                rel = os.path.relpath(raw_path, directory)
            except Exception:
                rel = raw_path
        else:
            rel = raw_path
        return rel.replace("\\", "/").lstrip("./")

    file_rels = [_to_rel(p) for p in files if p]

    summarized_rels: list[str] = []
    summarized_modes: dict[str, str] = {}
    root_summarized = False
    if summarized_folders:
        if isinstance(summarized_folders, dict):
            for raw_path, mode in summarized_folders.items():
                rel = _to_rel(raw_path).rstrip("/")
                if rel:
                    summarized_modes[rel] = str(mode or "")
                    summarized_rels.append(rel)
        else:
            summarized_rels = [_to_rel(p).rstrip("/") for p in summarized_folders if p is not None]
            for rel in summarized_rels:
                summarized_modes[rel] = "FOLDER_SUMMARY_QUICK"
        for rel in summarized_rels:
            if rel in {"", "."}:
                root_summarized = True
                break

    pinned_set: set[str] = set()
    if pinned_files is not None:
        pinned_set = {_to_rel(p) for p in pinned_files if p}

    if summarized_rels:
        summarized_prefixes: list[str] = []
        for rel in summarized_rels:
            if rel in {"", "."}:
                summarized_prefixes.append("")
            else:
                summarized_prefixes.append(rel.rstrip("/") + "/")

        def _covered_by_summary(path: str) -> bool:
            for pref in summarized_prefixes:
                if pref == "":
                    return True
                if path.startswith(pref):
                    return True
            return False

        file_rels = [
            p for p in file_rels if not _covered_by_summary(p) or p in pinned_set
        ]

    def _summary_marker(mode: str) -> str:
        m = str(mode or "").strip().upper()
        if m == "FOLDER_SUMMARY_FULL":
            return "  [FOLDER_SUMMARY_FULL]"
        if m in {"FOLDER_SUMMARY_QUICK", "FOLDER_SUMMARY"}:
            return "  [FOLDER_SUMMARY_QUICK]"
        if m:
            return f"  [{m}]"
        return "  [FOLDER_SUMMARY]"

    root_mode = summarized_modes.get("", summarized_modes.get(".", "FOLDER_SUMMARY_QUICK"))
    root_marker = _summary_marker(root_mode) if root_summarized else ""
    tree += f"{tee}'{base_dir}'{root_marker}\n"

    structure: dict[str, dict] = {}

    def _ensure_folder(parts: list[str]) -> dict:
        current = structure
        for part in parts:
            current = current.setdefault(part, {})
        return current

    for folder_rel in summarized_rels:
        if not folder_rel or folder_rel == ".":
            continue
        parts = [p for p in folder_rel.split("/") if p]
        node = _ensure_folder(parts)
        node["__summary_mode__"] = summarized_modes.get(folder_rel, "FOLDER_SUMMARY_QUICK")

    for rel_path in file_rels:
        if not rel_path:
            continue
        parts = rel_path.split("/")
        current = structure
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current.setdefault("__files__", []).append(parts[-1])

    def _render(node: dict, prefix: str = vertical) -> str:
        output_lines = []
        keys = sorted(
            k for k in node.keys() if k not in {"__files__", "__summary_mode__"}
        )
        for index, dirname in enumerate(keys):
            child = node.get(dirname)
            if not isinstance(child, dict):
                continue
            is_last = index == len(keys) - 1 and "__files__" not in node
            branch = last if is_last else tee
            child_prefix = prefix + ("    " if is_last else vertical)
            summary_mode = child.get("__summary_mode__")
            marker = _summary_marker(summary_mode) if summary_mode else ""
            output_lines.append(f"{prefix}{branch}'{dirname}'/{marker}\n")
            output_lines.append(_render(child, child_prefix))
        if "__files__" in node:
            files_here = sorted(node["__files__"])
            for index, filename in enumerate(files_here):
                branch = last if index == len(files_here) - 1 else tee
                output_lines.append(f"{prefix}{branch}{filename}\n")
        return "".join(output_lines)

    tree += _render(structure, vertical)
    return tree


__all__ = [
    "ScanError",
    "find_always_include_files",
    "generate_file_tree",
    "gather_files",
    "scan_directory",
    "scan_file_list",
    "append_files_to_context",
]
