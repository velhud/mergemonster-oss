from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


PORTABLE_ROOT_HEADER = "ROOT FOLDER: '.'\n\n"


@dataclass(frozen=True)
class RenderDecision:
    write_code: bool
    write_summary: bool
    write_skeleton: bool
    summary_required: bool
    skeleton_required: bool
    requested_code_plus: bool
    emit_summary_failed_comment: bool


def normalize_mode(mode: str | None) -> str:
    return (str(mode or "").strip().lower() if mode else "")


def summary_flags(mode_norm: str, content_mode: str) -> tuple[Optional[str], bool, bool]:
    summary_required = False
    summary_kind: Optional[str] = None
    requested_code_plus = False

    if mode_norm in {
        "summary_short",
        "summary_long",
        "code_plus_short",
        "code_plus_long",
        "summary_short_plus_skeleton",
        "summary_long_plus_skeleton",
        "code_plus_summary_short_plus_skeleton",
        "code_plus_summary_long_plus_skeleton",
    }:
        summary_required = True
    elif not mode_norm and content_mode in (
        "code_plus_short",
        "code_plus_long",
        "short_only",
        "long_only",
        "short_plus_skeleton",
        "long_plus_skeleton",
        "code_plus_short_plus_skeleton",
        "code_plus_long_plus_skeleton",
    ):
        summary_required = True

    if mode_norm in {
        "summary_short",
        "code_plus_short",
        "summary_short_plus_skeleton",
        "code_plus_summary_short_plus_skeleton",
    }:
        summary_kind = "short"
    elif mode_norm in {
        "summary_long",
        "code_plus_long",
        "summary_long_plus_skeleton",
        "code_plus_summary_long_plus_skeleton",
    }:
        summary_kind = "long"
    elif not mode_norm and content_mode in (
        "code_plus_short",
        "short_only",
        "short_plus_skeleton",
        "code_plus_short_plus_skeleton",
    ):
        summary_kind = "short"
    elif not mode_norm and content_mode in (
        "code_plus_long",
        "long_only",
        "long_plus_skeleton",
        "code_plus_long_plus_skeleton",
    ):
        summary_kind = "long"

    if mode_norm in {
        "code_plus_short",
        "code_plus_long",
        "code_plus_summary_short_plus_skeleton",
        "code_plus_summary_long_plus_skeleton",
    }:
        requested_code_plus = True
    elif not mode_norm and content_mode in (
        "code_plus_short",
        "code_plus_long",
        "code_plus_short_plus_skeleton",
        "code_plus_long_plus_skeleton",
    ):
        requested_code_plus = True

    return summary_kind, summary_required, requested_code_plus


def skeleton_required_for(mode_norm: str, content_mode: str) -> bool:
    if mode_norm in {
        "skeleton",
        "code_plus_skeleton",
        "summary_short_plus_skeleton",
        "summary_long_plus_skeleton",
        "code_plus_summary_short_plus_skeleton",
        "code_plus_summary_long_plus_skeleton",
    }:
        return True
    return not mode_norm and content_mode in {
        "skeleton_only",
        "code_plus_skeleton",
        "short_plus_skeleton",
        "long_plus_skeleton",
        "code_plus_short_plus_skeleton",
        "code_plus_long_plus_skeleton",
    }


def check_strict_summary(
    *,
    strict_summary_required: bool,
    summary_required: bool,
    summary_available: bool,
    summary_error: bool,
    abs_path: str,
) -> None:
    if strict_summary_required and summary_required:
        if summary_error or not summary_available:
            raise RuntimeError(f"Summary required but unavailable for {abs_path}")


def decide_render(
    *,
    mode_norm: str,
    content_mode: str,
    summary_required: bool,
    requested_code_plus: bool,
    summary_available: bool,
    summary_error: bool,
    fallback_to_code_on_summary_error: bool,
    strict_summary_required: bool,
) -> RenderDecision:
    write_code = False
    write_summary = False
    write_skeleton = skeleton_required_for(mode_norm, content_mode)

    if mode_norm == "code":
        write_code = True
    elif mode_norm == "skeleton":
        write_skeleton = True
    elif mode_norm in {"summary_short", "summary_long"}:
        write_summary = True
    elif mode_norm == "code_plus_skeleton":
        write_code = True
        write_skeleton = True
    elif mode_norm in {"summary_short_plus_skeleton", "summary_long_plus_skeleton"}:
        write_summary = True
        write_skeleton = True
    elif mode_norm in {
        "code_plus_summary_short_plus_skeleton",
        "code_plus_summary_long_plus_skeleton",
    }:
        write_code = True
        write_summary = True
        write_skeleton = True
    elif mode_norm in {"code_plus_short", "code_plus_long"}:
        write_code = True
        write_summary = True
    else:
        if content_mode == "code_only":
            write_code = True
        elif content_mode == "skeleton_only":
            write_skeleton = True
        elif content_mode == "code_plus_skeleton":
            write_code = True
            write_skeleton = True
        elif content_mode in ("code_plus_short", "code_plus_long"):
            write_code = True
            write_summary = True
        elif content_mode in ("short_plus_skeleton", "long_plus_skeleton"):
            write_summary = True
            write_skeleton = True
        elif content_mode in ("code_plus_short_plus_skeleton", "code_plus_long_plus_skeleton"):
            write_code = True
            write_summary = True
            write_skeleton = True
        elif content_mode in ("short_only", "long_only"):
            if summary_available:
                write_summary = True
            else:
                write_code = True

    if not (write_code or write_summary or write_skeleton):
        write_code = True

    emit_summary_failed_comment = False
    if write_summary and summary_error and not strict_summary_required:
        if fallback_to_code_on_summary_error and not (write_skeleton and not requested_code_plus):
            emit_summary_failed_comment = True
            write_summary = False
            write_code = True
        elif requested_code_plus:
            write_code = False

    if (
        requested_code_plus
        and not fallback_to_code_on_summary_error
        and not summary_available
        and not strict_summary_required
    ):
        write_code = False

    return RenderDecision(
        write_code=write_code,
        write_summary=write_summary,
        write_skeleton=write_skeleton,
        summary_required=summary_required,
        skeleton_required=write_skeleton,
        requested_code_plus=requested_code_plus,
        emit_summary_failed_comment=emit_summary_failed_comment,
    )


def escape_file_display(path: str) -> str:
    return str(path).replace("'", "\\'")


def format_label(label: str, file_display: str) -> str:
    return str(label or "").replace("{file_display}", file_display)


__all__ = [
    "RenderDecision",
    "check_strict_summary",
    "decide_render",
    "escape_file_display",
    "format_label",
    "normalize_mode",
    "skeleton_required_for",
    "summary_flags",
]
