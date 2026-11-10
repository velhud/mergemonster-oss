"""File classification helpers shared across MergeMonster."""

from __future__ import annotations

import os
from typing import Callable, Mapping, Sequence

from codemerge.constants import EXT_GROUPS
from codemerge.models import Category

FRONTEND_EXT = {
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".storyboard",
    ".xib",
}
DOC_EXT = {".md", ".mdx", ".rst", ".txt"}
CONFIG_EXT = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".lock",
    ".cfg",
    ".cnf",
    ".plist",
    ".pbxproj",
    ".xcconfig",
    ".strings",
    ".entitlements",
    ".xcworkspacedata",
    ".xcscheme",
    ".xcuserstate",
    ".xcsettings",
}
ASSET_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".bmp",
    ".webp",
    ".mp4",
    ".mp3",
    ".wav",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".eot",
}

TEXT_EXT: set[str] = {
    f".{ext}" for grp in ("CODE", "MARKUP", "DATA") for ext in EXT_GROUPS.get(grp, set())
}
TEXT_EXT.update(DOC_EXT)
TEXT_EXT.update(CONFIG_EXT)

BINARY_EXT: set[str] = {f".{ext}" for ext in EXT_GROUPS.get("BINARY", set())}
BINARY_EXT.update(ASSET_EXT)

_STATIC_PATH_HINTS: Sequence[str] = ("/static/", "/public/")


def categorise(
    path: str,
    is_binary_hint: bool = False,
    *,
    custom_ext_map: Mapping[str, Category] | None = None,
    binary_check: Callable[[str], bool] | None = None,
) -> Category:
    """Return the :class:`Category` for ``path`` given optional hints."""
    lowered = path.lower().replace("\\", "/")
    ext = os.path.splitext(lowered)[1]

    if custom_ext_map:
        mapped = custom_ext_map.get(ext.lstrip("."))
        if mapped:
            return mapped

    if any(hint in lowered for hint in _STATIC_PATH_HINTS):
        return Category.FRONTEND

    if ext in ASSET_EXT:
        return Category.ASSETS
    if ext in DOC_EXT or "/docs/" in lowered:
        return Category.DOCS
    if ext in FRONTEND_EXT:
        return Category.FRONTEND
    if ext in CONFIG_EXT:
        return Category.CONFIG
    if ext in TEXT_EXT:
        return Category.BACKEND

    if is_binary_hint:
        return Category.BINARY

    if binary_check and binary_check(path):
        return Category.BINARY

    return Category.BACKEND


__all__ = [
    "ASSET_EXT",
    "BINARY_EXT",
    "CONFIG_EXT",
    "DOC_EXT",
    "FRONTEND_EXT",
    "TEXT_EXT",
    "categorise",
]
