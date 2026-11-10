from __future__ import annotations


def render_folder_summary(folder_abs: str, summary_text: str, *, profile: str = "merge") -> str:
    _ = folder_abs
    _ = profile
    note = "Quick orientation summary. May omit areas. Use Full for coverage-preserving maps."
    body = str(summary_text or "").strip()
    if not body:
        return note
    return f"{note}\n\n{body}"


def render_repo_summary(summary_text: str, *, profile: str = "merge") -> str:
    _ = profile
    note = "Quick orientation summary. May omit areas. Use Full for coverage-preserving maps."
    body = str(summary_text or "").strip()
    if not body:
        return note
    return f"{note}\n\n{body}"


__all__ = ["render_folder_summary", "render_repo_summary"]
