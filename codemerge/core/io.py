"""File IO helpers shared by scanner and UI components."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Tuple

from codemerge.logger import get_logger
from codemerge.models import Category

from .classify import BINARY_EXT, TEXT_EXT

logger = get_logger(__name__)


def _normalise_ext(path: str) -> str:
    return Path(path).suffix.lower()


def is_binary_file(
    file_path: str,
    *,
    chunk_size: int = 2048,
    custom_ext_map: Mapping[str, Category] | None = None,
) -> bool:
    """Return True if file at ``file_path`` appears to be binary."""
    ext = _normalise_ext(file_path)
    mapped = custom_ext_map.get(ext.lstrip(".")) if custom_ext_map else None
    if mapped in (Category.BINARY, Category.ASSETS) or (mapped is None and ext in BINARY_EXT):
        return True
    if not Path(file_path).is_file():
        return True

    try:
        with open(file_path, "rb") as handle:
            chunk = handle.read(max(1, chunk_size))
            if not chunk:
                return False
            if b"\x00" in chunk:
                return True
            if mapped is not None or ext in TEXT_EXT:
                return False
            try:
                chunk.decode("utf-8")
                return False
            except UnicodeDecodeError:
                return True
    except Exception as exc:
        logger.warning("Failed to read file %s for binary check: %s", file_path, exc)
        return False


def read_file_content(
    file_path: str,
    *,
    custom_ext_map: Mapping[str, Category] | None = None,
) -> str | None:
    """Return UTF-8 text content for ``file_path`` unless it is binary."""
    try:
        if not _should_read_text(file_path, custom_ext_map=custom_ext_map):
            return None
        with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    except (PermissionError, IOError) as exc:
        logger.error("Could not read file %s: %s", file_path, exc)
        return None


def read_file_content_ex(
    file_path: str,
    *,
    custom_ext_map: Mapping[str, Category] | None = None,
) -> Tuple[str | None, str]:
    """Return (content, status) for ``file_path`` where status is 'ok' or 'binary'/'error'."""
    try:
        if not _should_read_text(file_path, custom_ext_map=custom_ext_map):
            return None, "binary"
        with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read(), "ok"
    except (PermissionError, IOError) as exc:
        logger.error("Could not read file %s: %s", file_path, exc)
        return None, "error"


def _should_read_text(
    file_path: str,
    *,
    custom_ext_map: Mapping[str, Category] | None = None,
) -> bool:
    return not is_binary_file(file_path, custom_ext_map=custom_ext_map)


__all__ = ["is_binary_file", "read_file_content", "read_file_content_ex"]
