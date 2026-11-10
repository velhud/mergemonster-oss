"""Utility helpers for prune JSON assets."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

__all__ = ["load_json_asset"]


def load_json_asset(name: str) -> Any:
    """Load the bundled JSON payload for pruning helpers."""
    resource = files(__name__).joinpath(name)
    with resource.open("r", encoding="utf-8") as handle:
        return json.load(handle)
