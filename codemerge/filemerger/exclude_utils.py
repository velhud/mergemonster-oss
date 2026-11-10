from __future__ import annotations

import os
from typing import Iterable, Set, Tuple


def norm_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def split_excluded_dirs(items: Iterable[str] | None) -> Tuple[Set[str], Set[str]]:
    rel: Set[str] = set()
    abs_paths: Set[str] = set()
    for item in items or []:
        if not isinstance(item, str):
            continue
        raw = item.strip()
        if not raw:
            continue
        norm = norm_path(raw)
        if os.path.isabs(raw):
            abs_paths.add(norm)
        else:
            rel.add(norm)
    return rel, abs_paths


def split_excluded_files(items: Iterable[str] | None) -> Tuple[Set[str], Set[str], Set[str]]:
    names: Set[str] = set()
    rel: Set[str] = set()
    abs_paths: Set[str] = set()
    for item in items or []:
        if not isinstance(item, str):
            continue
        raw = item.strip()
        if not raw:
            continue
        norm = norm_path(raw)
        if os.path.isabs(raw):
            abs_paths.add(norm)
        else:
            if os.sep in norm or (os.altsep and os.altsep in norm):
                rel.add(norm)
            else:
                names.add(norm)
    return names, rel, abs_paths


def is_abs_excluded(path_norm: str, excluded_abs: Iterable[str]) -> bool:
    for ex in excluded_abs:
        if path_norm == ex or path_norm.startswith(ex + os.sep):
            return True
    return False
