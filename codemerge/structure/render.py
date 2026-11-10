from __future__ import annotations

from .models import StructuralFileView


def _ratio_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def render_skeleton_block(view: StructuralFileView) -> str:
    lines = [
        "# SKELETON",
        f"# language: {view.language}",
        f"# parser_status: {view.parser_status}",
        f"# source_tokens: {view.token_count_source if view.token_count_source is not None else 'unknown'}",
        f"# skeleton_tokens: {view.token_count_skeleton if view.token_count_skeleton is not None else 'unknown'}",
        f"# compression: {_ratio_text(view.compression_ratio)}",
        f"# symbols: {view.symbol_count}",
    ]
    for warning in view.warnings:
        lines.append(f"# warning: {warning}")
    if view.skeleton_text.strip():
        lines.append("")
        lines.append(view.skeleton_text.rstrip())
    else:
        lines.append("")
        lines.append("# Skeleton unavailable for this file.")
    return "\n".join(lines).rstrip() + "\n"
