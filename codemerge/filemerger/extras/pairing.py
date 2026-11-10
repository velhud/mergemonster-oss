from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from codemerge.logger import get_logger

from .restore import collect_source_files
from ..types import FileProcessorLike as FileProcessor

logger = get_logger(__name__)


def _normalise_pair_rel(rel: str) -> str | None:
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or os.path.isabs(raw):
        return None
    text = raw.lstrip("/")
    if not text or ":" in Path(text).parts[0]:
        return None
    path = Path(text)
    if ".." in path.parts:
        return None
    return path.as_posix()


def _pair_output_path(dest_dir: str, rel: str, *, keep_structure: bool) -> str | None:
    rel_norm = _normalise_pair_rel(rel)
    if rel_norm is None:
        return None
    base = Path(dest_dir).resolve()
    if keep_structure:
        target = (base / rel_norm).resolve()
    else:
        target = (base / rel_norm.replace("/", "__")).resolve()
    if target != base and base in target.parents:
        return str(target)
    return None


def pair_codebases(
    processor: "FileProcessor",
    src_a: str,
    src_b: str,
    dest_dir: str,
    keep_structure: bool = True,
    label_a: Optional[str] = None,
    label_b: Optional[str] = None,
) -> None:
    """Create paired files from two codebases or merged files."""

    logger.info(
        "pair_codebases start: src_a=%s src_b=%s dest=%s keep=%s",
        src_a,
        src_b,
        dest_dir,
        keep_structure,
    )

    label_a = label_a or src_a
    label_b = label_b or src_b

    files_a = collect_source_files(processor, src_a)
    files_b = collect_source_files(processor, src_b)

    all_keys = set(files_a.keys()) | set(files_b.keys())

    for rel in all_keys:
        rel_norm = _normalise_pair_rel(rel)
        if rel_norm is None:
            logger.warning("Skipping unsafe pair output path: %s", rel)
            continue
        content_a = files_a.get(rel, "")
        content_b = files_b.get(rel, "")

        target_rel = rel_norm
        if not target_rel:
            base_a = os.path.basename(src_a)
            base_b = os.path.basename(src_b)
            target_rel = base_a if content_a else base_b

        out_path = _pair_output_path(dest_dir, target_rel, keep_structure=keep_structure)
        if out_path is None:
            logger.warning("Skipping pair output outside destination: %s", target_rel)
            continue

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(f"======= START OF ORIGINAL: {label_a} =======\n")
            handle.write(content_a)
            handle.write("\n======= END OF ORIGINAL =======\n\n")
            handle.write(f"======= START OF NEW: {label_b} =======\n")
            handle.write(content_b)
            handle.write("\n======= END OF NEW =======\n")

    logger.info("pair_codebases complete")


def pair_selected_files(
    processor: "FileProcessor",
    directory: str,
    selected_files: List[str],
    other_source: str,
    dest_dir: str,
    keep_structure: bool = True,
    label_a: Optional[str] = None,
    label_b: Optional[str] = None,
) -> None:
    """Pair selected files from one directory with another source."""

    logger.info(
        "pair_selected_files start: dir=%s other=%s dest=%s keep=%s",
        directory,
        other_source,
        dest_dir,
        keep_structure,
    )

    label_a = label_a or f"{directory} (selected)"
    label_b = label_b or other_source

    files_other = collect_source_files(processor, other_source)

    directory_root = Path(directory).resolve()
    for rel in selected_files:
        rel_norm = _normalise_pair_rel(rel)
        if rel_norm is None:
            logger.warning("Skipping unsafe selected file path: %s", rel)
            continue
        path_a = (directory_root / rel_norm).resolve()
        if path_a != directory_root and directory_root not in path_a.parents:
            logger.warning("Skipping selected file outside source root: %s", rel)
            continue

        if not os.path.exists(path_a):
            logger.warning("Selected file missing: %s", path_a)
            continue

        content_a = processor.read_file_content(path_a)
        if content_a is None:
            content_a = f"# [binary file omitted: {rel}]\n"
        content_b = files_other.get(rel_norm)

        if content_b is None:
            logger.info("No counterpart for %s in other source", rel_norm)
            continue

        out_path = _pair_output_path(dest_dir, rel_norm, keep_structure=keep_structure)
        if out_path is None:
            logger.warning("Skipping pair output outside destination: %s", rel_norm)
            continue

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(f"======= START OF ORIGINAL: {label_a} =======\n")
            handle.write(content_a)
            handle.write("\n======= END OF ORIGINAL =======\n\n")
            handle.write(f"======= START OF NEW: {label_b} =======\n")
            handle.write(content_b)
            handle.write("\n======= END OF NEW =======\n")

    logger.info("pair_selected_files complete")
