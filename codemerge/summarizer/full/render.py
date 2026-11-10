from __future__ import annotations


def render_folder_summary_full(folder_abs: str, summary_text: str, *, profile: str = "merge") -> str:
    _ = folder_abs
    _ = profile
    return str(summary_text or "").strip()


def render_repo_summary_full(summary_text: str, *, profile: str = "merge") -> str:
    _ = profile
    return str(summary_text or "").strip()


__all__ = ["render_folder_summary_full", "render_repo_summary_full"]
