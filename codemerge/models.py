from __future__ import annotations

from dataclasses import dataclass
import sys as _sys
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List

DEFAULT_TOKEN_MAX = 10_000_000
DEFAULT_SIZE_MAX = 100_000.0  # KB



class FileMode(str, Enum):
    """Per-file merge decision."""

    EXCLUDE = "EXCLUDE"
    CODE = "CODE"
    SKELETON = "SKELETON"
    API_SURFACE = "API_SURFACE"
    SIGNATURES = "SIGNATURES"
    CODE_PLUS_SKELETON = "CODE_PLUS_SKELETON"
    CODE_PLUS_SHORT = "CODE_PLUS_SHORT"
    CODE_PLUS_LONG = "CODE_PLUS_LONG"
    SUMMARY_SHORT_PLUS_SKELETON = "SUMMARY_SHORT_PLUS_SKELETON"
    SUMMARY_LONG_PLUS_SKELETON = "SUMMARY_LONG_PLUS_SKELETON"
    CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON = "CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON"
    CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON = "CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON"
    SUMMARY_SHORT = "SUMMARY_SHORT"
    SUMMARY_LONG = "SUMMARY_LONG"


class Category(Enum):
    """High-level semantic bucket; may expand in later sprints."""

    BACKEND = auto()
    FRONTEND = auto()
    DOCS = auto()
    CONFIG = auto()
    ASSETS = auto()
    BINARY = auto()


_DC_KW = {"slots": True} if _sys.version_info >= (3, 10) else {}

@dataclass(**_DC_KW)
class FileMeta:
    """Immutable record describing a single file."""

    abs_path: Path
    rel_path: Path
    size_bytes: int
    mtime: float
    token_count: int | None
    ext: str
    category: Category
    excluded: bool = False
    read_error: bool = False


@dataclass(**_DC_KW)
class ScanContext:
    """
    Aggregate of one directory scan. ``files`` is unsorted; callers may
    sort or copy it as needed.
    """

    root: Path
    files: List[FileMeta]
    aggregate_tokens: int
    aggregate_bytes: int
    by_ext: Dict[str, List[int]]
    by_category: Dict[Category, List[int]]


__all__ = (
    "FileMode",
    "Category",
    "FileMeta",
    "ScanContext",
    "DEFAULT_TOKEN_MAX",
    "DEFAULT_SIZE_MAX",
)
