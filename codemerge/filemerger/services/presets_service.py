from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from ...models import Category, DEFAULT_SIZE_MAX, DEFAULT_TOKEN_MAX
from .dto import UiState


def build_snapshot(ui: UiState, *, extras: Dict[str, object]) -> Dict[str, object]:
    """Create a serialisable preset snapshot from the current UI state."""

    file_types = dict(ui.file_types)
    category_mask = {c.name: enabled for c, enabled in ui.filters.category_mask.items()}

    last_filters = {
        "categories": [c for c, enabled in category_mask.items() if enabled],
        "token_min": ui.filters.token_min,
        "token_max": ui.filters.token_max,
        "size_min": ui.filters.size_min_kb,
        "size_max": ui.filters.size_max_kb,
        "name_pattern": ui.filters.name_pattern,
        "date_after": ui.filters.date_after_ts,
        "date_before": ui.filters.date_before_ts,
        "date_after_manual": bool(extras.get("date_after_manual", False)),
        "date_before_manual": bool(extras.get("date_before_manual", False)),
    }

    snapshot = {
        "include_all": ui.scan.include_all,
        "file_types": file_types,
        "ignore_binary_files": ui.scan.ignore_binary,
        "max_file_size_mb": ui.scan.max_file_size_mb,
        "follow_symlinks": ui.scan.follow_symlinks,
        "exclude_ios_noise": bool(extras.get("exclude_ios_noise", False)),
        "exclude_node_modules": bool(extras.get("exclude_node_modules", False)),
        "exclude_python_noise": bool(extras.get("exclude_python_noise", False)),
        "exclude_java_gradle_noise": bool(extras.get("exclude_java_gradle_noise", False)),
        "exclude_web_build_noise": bool(extras.get("exclude_web_build_noise", False)),
        "always_include_docs": ui.filters.always_include_docs,
        "always_include_docs_list": list(ui.filters.always_include_list),
        "name_filter_glob": ui.filters.name_glob,
        "show_totals_columns": bool(extras.get("show_totals_columns", False)),
        "show_density_column": bool(extras.get("show_density_column", False)),
        "show_bytes_footer": bool(extras.get("show_bytes_footer", True)),
        "category_mask": category_mask,
        "last_filters": last_filters,
        "sort_by": ui.scan.sort_by,
        "sort_descending": ui.scan.sort_desc,
        "view_mode": ui.view_mode,
    }

    return snapshot


def apply_preset(preset: Dict[str, object], current: Dict[str, object]) -> Dict[str, object]:
    """Return normalized values derived from *preset*, falling back to *current* defaults."""

    def pick_bool(key: str, default_key: Optional[str] = None, default: bool = False) -> bool:
        base = current.get(default_key or key, default)
        return bool(preset.get(key, base))

    def pick_float(key: str, default_key: Optional[str] = None, default: float = 0.0) -> float:
        base = current.get(default_key or key, default)
        try:
            return float(preset.get(key, base))
        except Exception:
            return float(base)

    def pick_int(key: str, default_key: Optional[str] = None, default: int = 0) -> int:
        base = current.get(default_key or key, default)
        try:
            return int(preset.get(key, base))
        except Exception:
            return int(base)

    include_all = pick_bool("include_all", default=False)
    file_types = current.get("file_types", {})
    preset_file_types = preset.get("file_types", {})
    if include_all:
        ft_map = {ext: True for ext in file_types}
    else:
        ft_map = {ext: bool(preset_file_types.get(ext, file_types.get(ext, False))) for ext in file_types}

    category_mask_current = current.get("category_mask", {c.name: True for c in Category})
    preset_cat = preset.get("category_mask") or {}
    normalized_cat = {
        c.name: bool(preset_cat.get(c.name, category_mask_current.get(c.name, True)))
        for c in Category
    }

    last_filters = preset.get("last_filters") or {}
    filters = {
        "token_min": pick_int("token_min", default=0, default_key="token_min") if "token_min" in preset else int(last_filters.get("token_min", current.get("token_min", 0))),
        "token_max": pick_int("token_max", default=DEFAULT_TOKEN_MAX, default_key="token_max") if "token_max" in preset else int(last_filters.get("token_max", current.get("token_max", DEFAULT_TOKEN_MAX))),
        "size_min": float(last_filters.get("size_min", current.get("size_min", 0.0))),
        "size_max": float(last_filters.get("size_max", current.get("size_max", DEFAULT_SIZE_MAX))),
        "name_pattern": str(last_filters.get("name_pattern", current.get("name_pattern", ""))),
        "date_after": float(last_filters.get("date_after", current.get("date_after", 0.0))),
        "date_before": float(last_filters.get("date_before", current.get("date_before", 0.0))),
        "date_after_manual": bool(last_filters.get("date_after_manual", current.get("date_after_manual", False))),
        "date_before_manual": bool(last_filters.get("date_before_manual", current.get("date_before_manual", False))),
    }

    results = {
        "include_all": include_all,
        "file_types": ft_map,
        "ignore_binary_files": pick_bool("ignore_binary_files", default_key="ignore_binary_files", default=False),
        "max_file_size_mb": pick_float("max_file_size_mb", default_key="max_file_size_mb", default=0.0),
        "follow_symlinks": pick_bool("follow_symlinks", default_key="follow_symlinks", default=False),
        "exclude_ios_noise": pick_bool("exclude_ios_noise", default_key="exclude_ios_noise", default=True),
        "exclude_node_modules": pick_bool("exclude_node_modules", default_key="exclude_node_modules", default=True),
        "exclude_python_noise": pick_bool("exclude_python_noise", default_key="exclude_python_noise", default=False),
        "exclude_java_gradle_noise": pick_bool("exclude_java_gradle_noise", default_key="exclude_java_gradle_noise", default=False),
        "exclude_web_build_noise": pick_bool("exclude_web_build_noise", default_key="exclude_web_build_noise", default=False),
        "always_include_docs": pick_bool("always_include_docs", default_key="always_include_docs", default=True),
        "always_include_docs_list": list(preset.get("always_include_docs_list", current.get("always_include_docs_list", []))),
        "name_filter_glob": pick_bool("name_filter_glob", default_key="name_filter_glob", default=False),
        "show_totals_columns": pick_bool("show_totals_columns", default_key="show_totals_columns", default=False),
        "show_density_column": pick_bool("show_density_column", default_key="show_density_column", default=False),
        "show_bytes_footer": pick_bool("show_bytes_footer", default_key="show_bytes_footer", default=True),
        "category_mask": normalized_cat,
        "filters": filters,
        "sort_by": str(preset.get("sort_by", current.get("sort_by", "name"))),
        "sort_descending": pick_bool("sort_descending", default_key="sort_descending", default=False),
        "view_mode": str(preset.get("view_mode", current.get("view_mode", "hierarchy"))),
    }

    return results


def save_preset(store: Dict[str, Dict[str, object]], name: str, preset: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    """Return a new presets mapping with *name* stored."""

    updated = dict(store)
    updated[name] = preset
    return updated


def load_preset(store: Dict[str, Dict[str, object]], name: str) -> Dict[str, object]:
    """Return the preset named *name* or raise ``KeyError``."""

    if name not in store:
        raise KeyError(name)
    return dict(store[name])


def delete_preset(store: Dict[str, Dict[str, object]], name: str) -> Dict[str, Dict[str, object]]:
    """Return a new presets mapping without *name*. Missing names are ignored."""

    updated = dict(store)
    updated.pop(name, None)
    return updated


def export_preset(preset: Dict[str, object], path: str, name: str) -> None:
    """Write *preset* to *path* using the on-disk JSON structure."""

    payload = {"name": name, "preset": preset}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def import_preset(path: str) -> Tuple[str, Dict[str, object]]:
    """Load a preset from *path* and return ``(name, preset_dict)``."""

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    if "preset" in data and "name" in data:
        name = str(data["name"])
        preset = data["preset"]
    else:
        name = Path(path).stem
        preset = data

    if not isinstance(preset, dict):
        raise ValueError("Preset file must contain a dict of settings")

    return name, preset
