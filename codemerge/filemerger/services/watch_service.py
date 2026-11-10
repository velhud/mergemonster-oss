from __future__ import annotations

import os
from typing import Optional, Set, Tuple

from .. import FileProcessor
from ..scan import _regular_file_in_root, _walk_directory_allowed
from ..exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)
from .dto import UiState


def compute_fingerprint(
    fp: FileProcessor,
    ui: UiState,
    sample_limit: int = 3000,
    *,
    excluded_dirs: Optional[Set[str]] = None,
    excluded_files: Optional[Set[str]] = None,
) -> Tuple[int, float]:
    """Return a lightweight (count, max_mtime) fingerprint for watch mode."""

    root = str(ui.root_dir)
    if not root or not os.path.isdir(root):
        return (0, 0.0)

    limit = max(100, int(sample_limit) if sample_limit else 3000)
    max_mtime = 0.0
    total = 0

    try:
        if ui.scan.use_git_files:
            files = fp.git_files(root, include_untracked=ui.scan.git_include_untracked)
            total = len(files)
            for path in files[:limit]:
                try:
                    mt = os.path.getmtime(path)
                except Exception:
                    continue
                if mt > max_mtime:
                    max_mtime = mt
            return (total, max_mtime)

        excluded_dirs = excluded_dirs if excluded_dirs is not None else set(getattr(fp, "excluded_dirs", set()))
        excluded_files = excluded_files if excluded_files is not None else set(getattr(fp, "excluded_files", set()))
        excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(excluded_dirs)
        excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(excluded_files)

        seen = 0
        canonical_root = os.path.realpath(root)
        visited = {canonical_root}
        follow_links = ui.scan.follow_symlinks
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_links):
            pruned: list[str] = []
            for name in list(dirnames):
                abs_dir = os.path.join(dirpath, name)
                rel = norm_path(os.path.relpath(abs_dir, root))
                name_norm = norm_path(name)
                abs_norm = norm_path(abs_dir)
                if (
                    not _walk_directory_allowed(abs_dir, canonical_root, follow_links, visited)
                    or name_norm in excluded_dirs_rel
                    or rel in excluded_dirs_rel
                    or (excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs))
                    or fp.is_virtual_env(abs_dir)
                ):
                    pruned.append(name)
            for name in pruned:
                try:
                    dirnames.remove(name)
                except ValueError:
                    pass

            for fname in filenames:
                fname_norm = norm_path(fname)
                if fname_norm in excluded_files_name:
                    continue
                full_path = os.path.join(dirpath, fname)
                if not _regular_file_in_root(full_path, canonical_root, follow_links):
                    continue
                full_norm = norm_path(full_path)
                if full_norm in excluded_files_abs:
                    continue
                rel_file = norm_path(os.path.relpath(full_path, root))
                if rel_file in excluded_files_rel:
                    continue
                try:
                    mt = os.path.getmtime(full_path)
                    if mt > max_mtime:
                        max_mtime = mt
                except Exception:
                    pass
                total += 1
                seen += 1
                if seen >= limit:
                    break
            if seen >= limit:
                break
    except Exception:
        return (0, 0.0)

    return (total, max_mtime)
