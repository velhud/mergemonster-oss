from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from codemerge.core import ASSET_EXT, BINARY_EXT
from codemerge.filemerger.types import FileProcessorLike as FileProcessor
from codemerge.models import FileMeta
from codemerge.summarizer.shared.manifest import manifest_hash


def posix(path: str) -> str:
    return str(path or "").replace("\\", "/")


def normalize_rel_path(path: str) -> str:
    rel = posix(str(path or "").strip())
    if rel.startswith("./"):
        rel = rel[2:]
    return rel


def safe_rel(path: str, root: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except Exception:
        rel = path
    return posix(rel)


def rel_has_parent_segment(rel: str) -> bool:
    parts = [p for p in posix(rel).split("/") if p]
    return any(p == ".." for p in parts)


def filter_folder_metas(
    folder_abs: str,
    root_abs: str,
    scan_files: Iterable[FileMeta],
) -> tuple[list[FileMeta], str]:
    folder_rel = safe_rel(folder_abs, root_abs)
    if folder_rel in {".", ""}:
        return list(scan_files), ""
    prefix = folder_rel.rstrip("/") + "/"
    out: list[FileMeta] = []
    for meta in scan_files:
        rel = posix(meta.rel_path.as_posix())
        if rel_has_parent_segment(rel):
            continue
        if rel.startswith(prefix):
            out.append(meta)
    return out, prefix


def split_direct_vs_descendant(
    metas: list[FileMeta],
    folder_prefix: str,
) -> tuple[list[FileMeta], dict[str, list[FileMeta]]]:
    direct: list[FileMeta] = []
    children: dict[str, list[FileMeta]] = {}
    for meta in metas:
        rel = posix(meta.rel_path.as_posix())
        rel_local = folder_rel_path(rel, folder_prefix)
        parts = [p for p in rel_local.split("/") if p]
        if not parts:
            continue
        if len(parts) == 1:
            direct.append(meta)
            continue
        child = parts[0]
        children.setdefault(child, []).append(meta)
    return direct, children


def build_manifest(metas: list[FileMeta]) -> tuple[list[str], str]:
    rel_paths = [posix(meta.rel_path.as_posix()) for meta in metas]
    entries = sorted(
        (
            posix(meta.rel_path.as_posix()),
            float(meta.mtime),
            int(meta.size_bytes),
        )
        for meta in metas
    )
    return rel_paths, manifest_hash(entries)


def folder_rel_path(rel: str, prefix: str) -> str:
    if prefix and rel.startswith(prefix):
        rel = rel[len(prefix):]
    return rel.lstrip("/")


def join_folder_rel(prefix: str, rel: str) -> str:
    prefix_norm = normalize_rel_path(prefix).rstrip("/")
    rel_norm = normalize_rel_path(rel).lstrip("/")
    if not prefix_norm:
        return rel_norm
    if not rel_norm:
        return prefix_norm
    return f"{prefix_norm}/{rel_norm}"


def inventory_in_scope(
    *,
    metas: list[FileMeta],
    file_processor: FileProcessor,
) -> tuple[list[FileMeta], list[dict], list[str]]:
    in_scope: list[FileMeta] = []
    skipped: list[dict] = []
    errors: list[str] = []
    for meta in metas:
        abs_path = str(meta.abs_path)
        rel = posix(meta.rel_path.as_posix())
        ext = (Path(abs_path).suffix or "").lower()
        if ext in BINARY_EXT or ext in ASSET_EXT:
            skipped.append({"path": rel, "reason": "binary_or_asset_ext"})
            continue
        if meta.read_error:
            skipped.append({"path": rel, "reason": "meta_read_error"})
            errors.append(rel)
            continue
        try:
            if file_processor.is_binary_file(abs_path):
                skipped.append({"path": rel, "reason": "binary_detected"})
                continue
        except Exception:
            skipped.append({"path": rel, "reason": "binary_check_failed"})
            errors.append(rel)
            continue
        try:
            content = file_processor.read_file_content(abs_path)
        except Exception:
            content = None
        if content is None:
            skipped.append({"path": rel, "reason": "unreadable_text"})
            errors.append(rel)
            continue
        in_scope.append(meta)
    return in_scope, skipped, errors


__all__ = [
    "posix",
    "normalize_rel_path",
    "safe_rel",
    "rel_has_parent_segment",
    "filter_folder_metas",
    "split_direct_vs_descendant",
    "build_manifest",
    "folder_rel_path",
    "join_folder_rel",
    "inventory_in_scope",
]
