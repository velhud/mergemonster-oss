"""Locations for user-owned MergeMonster state."""

from __future__ import annotations

import os
from pathlib import Path


def state_directory() -> Path:
    """Use the public location while honoring an existing local installation."""
    configured = os.environ.get("MERGEMONSTER_HOME") or os.environ.get("CODEMERGE_HOME")
    if configured:
        return Path(os.path.expandvars(configured)).expanduser().resolve()
    home = Path.home()
    current = home / ".mergemonster"
    legacy = home / ".codemerge"
    return legacy if not current.exists() and legacy.is_dir() else current
