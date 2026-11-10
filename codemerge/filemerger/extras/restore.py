from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from codemerge.logger import get_logger
from ..exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)
from ..types import FileProcessorLike as FileProcessor

logger = get_logger(__name__)


def extract_merged_files(
    merged_path: str,
    custom_start_label: str = "======= START OF FILE: {file_display} =======",
    custom_end_label: str = "======= END OF FILE: {file_display} =======",
) -> Dict[str, str]:
    """Return mapping of relative file paths to contents from a merged file or folder."""

    files: Dict[str, str] = {}
    paths: List[str] = []

    if os.path.isdir(merged_path):
        for name in os.listdir(merged_path):
            candidate = os.path.join(merged_path, name)
            if not os.path.islink(candidate) and os.path.isfile(candidate):
                paths.append(candidate)
    else:
        paths.append(merged_path)

    start_re = re.compile(
        re.escape(custom_start_label).replace(r"\{file_display\}", "(?P<display>.+)")
    )
    end_re = re.compile(
        re.escape(custom_end_label).replace(r"\{file_display\}", "(?P<display>.+)")
    )
    root_re = re.compile(r"ROOT FOLDER: '(?P<root>.+)'")

    def _parse_display(display: str, root_name: Optional[str]) -> str:
        text = str(display or "").strip()
        rel = text
        quoted = re.match(r"^'((?:\\'|[^'])*)'(?:\s+(.*))?$", text)
        if quoted:
            prefix = quoted.group(1).replace("\\'", "'")
            rest = (quoted.group(2) or "").strip()
            rel = prefix
            if rest:
                rest = rest.lstrip("/\\")
                rel = f"{prefix}/{rest}" if rest else prefix
        rel = rel.replace("\\", "/")
        if root_name:
            root_norm = str(root_name).replace("\\", "/").rstrip("/")
            if root_norm and rel.startswith(root_norm + "/"):
                rel = rel[len(root_norm) + 1 :]
            elif root_norm and rel == root_norm:
                rel = os.path.basename(root_norm)
        return rel

    for path in paths:
        root_name: Optional[str] = None
        current_rel: Optional[str] = None
        buffer: List[str] = []
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if root_name is None:
                    match_root = root_re.match(line.strip())
                    if match_root:
                        root_name = match_root.group("root")

                match_start = start_re.match(line.rstrip())
                if match_start:
                    display = match_start.group("display")
                    current_rel = _parse_display(display, root_name)
                    buffer = []
                    continue

                match_end = end_re.match(line.rstrip())
                if match_end and current_rel is not None:
                    files[current_rel] = "".join(buffer)
                    current_rel = None
                    buffer = []
                    continue

                if current_rel is not None:
                    buffer.append(line)

    return files


def collect_source_files(processor: "FileProcessor", source: str) -> Dict[str, str]:
    """Collect file contents from a directory or merged text file."""

    if os.path.isdir(source):
        excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(getattr(processor, "excluded_dirs", set()))
        excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(
            getattr(processor, "excluded_files", set())
        )
        mapping: Dict[str, str] = {}
        for root_dir, dirs, filenames in os.walk(source):
            dirs_to_remove: List[str] = []
            for d in dirs:
                dir_path = os.path.join(root_dir, d)
                rel_dir = norm_path(os.path.relpath(dir_path, source))
                dir_norm = norm_path(dir_path)
                name_norm = norm_path(d)
                if (
                    name_norm in excluded_dirs_rel
                    or rel_dir in excluded_dirs_rel
                    or (excluded_dirs_abs and is_abs_excluded(dir_norm, excluded_dirs_abs))
                    or processor.is_virtual_env(dir_path)
                ):
                    dirs_to_remove.append(d)

            for d in dirs_to_remove:
                if d in dirs:
                    dirs.remove(d)

            for name in filenames:
                name_norm = norm_path(name)
                if name_norm in excluded_files_name:
                    continue
                fpath = os.path.join(root_dir, name)
                fpath_norm = norm_path(fpath)
                if fpath_norm in excluded_files_abs:
                    continue
                rel_file = norm_path(os.path.relpath(fpath, source))
                if rel_file in excluded_files_rel:
                    continue

                if os.path.islink(fpath) or not os.path.isfile(fpath):
                    continue

                rel = os.path.relpath(fpath, source)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as handle:
                        mapping[rel] = handle.read()
                except (FileNotFoundError, PermissionError) as exc:
                    logger.warning("Failed to read file %s: %s", fpath, exc)
        return mapping

    return extract_merged_files(source)


def restore_from_merged(
    processor: "FileProcessor",
    merged_path: str,
    output_dir: str,
    custom_start_label: str = "======= START OF FILE: {file_display} =======",
    custom_end_label: str = "======= END OF FILE: {file_display} =======",
) -> None:
    """Rebuild original file tree from a merged file or directory of merged files."""

    files = extract_merged_files(
        merged_path,
        custom_start_label=custom_start_label,
        custom_end_label=custom_end_label,
    )
    base = Path(output_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        rel_path = str(rel_path or "").strip()
        if not rel_path:
            continue
        rel_path = rel_path.replace("\\", "/").lstrip("/")
        try:
            candidate = Path(rel_path)
        except Exception:
            logger.warning("Skipping invalid restore path: %s", rel_path)
            continue
        if candidate.is_absolute() or re.match(r"^[A-Za-z]:", rel_path):
            logger.warning("Skipping absolute restore path: %s", rel_path)
            continue
        if ".." in candidate.parts:
            logger.warning("Skipping unsafe restore path: %s", rel_path)
            continue
        if os.path.dirname(rel_path) == "":
            rel_path = os.path.join("__root__", rel_path)
        destination = (base / rel_path).resolve()
        if destination != base and base not in destination.parents:
            logger.warning("Skipping path outside restore root: %s", rel_path)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as out_file:
            out_file.write(content)
