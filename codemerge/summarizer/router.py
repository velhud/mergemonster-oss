from __future__ import annotations

from typing import Optional

from codemerge.filemerger.types import FileProcessorLike as FileProcessor
from codemerge.models import FileMeta
from codemerge.summarizer.quick.orchestrator import (
    ensure_folder_summary as ensure_folder_summary_quick,
    ensure_repo_summary as ensure_repo_summary_quick,
)
from codemerge.summarizer.quick.render import (
    render_folder_summary as render_folder_summary_quick,
    render_repo_summary as render_repo_summary_quick,
)
from codemerge.summarizer.full import (
    ensure_folder_summary_full,
    ensure_repo_summary_full,
)
from codemerge.summarizer.full.render import (
    render_folder_summary_full,
    render_repo_summary_full,
)
from codemerge.summarizer.full.coverage import get_full_coverage_proof


def _norm_mode(mode: str | None) -> str:
    raw = str(mode or "full").strip().lower()
    if raw in {"quick", "fast"}:
        return "quick"
    if raw == "full":
        return "full"
    raise ValueError(f"invalid summary mode: {mode!r}")


def ensure_folder_summary(
    *,
    folder_abs: str,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    mode: str | None = None,
    progress_cb: Optional[callable] = None,
    scope_source: str = "caller_scan_files",
) -> str:
    variant = _norm_mode(mode)
    if variant == "full":
        return ensure_folder_summary_full(
            folder_abs=folder_abs,
            root_abs=root_abs,
            scan_files=scan_files,
            file_processor=file_processor,
            app_config=app_config,
            progress_cb=progress_cb,
            scope_source=scope_source,
        )
    return ensure_folder_summary_quick(
        folder_abs=folder_abs,
        root_abs=root_abs,
        scan_files=scan_files,
        file_processor=file_processor,
        app_config=app_config,
        scope_source=scope_source,
    )


def ensure_repo_summary(
    *,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    mode: str | None = None,
    progress_cb: Optional[callable] = None,
    scope_source: str = "caller_scan_files",
) -> str:
    variant = _norm_mode(mode)
    if variant == "full":
        return ensure_repo_summary_full(
            root_abs=root_abs,
            scan_files=scan_files,
            file_processor=file_processor,
            app_config=app_config,
            progress_cb=progress_cb,
            scope_source=scope_source,
        )
    return ensure_repo_summary_quick(
        root_abs=root_abs,
        scan_files=scan_files,
        file_processor=file_processor,
        app_config=app_config,
        scope_source=scope_source,
    )


def render_folder_summary(
    folder_abs: str,
    summary_text: str,
    mode: str | None = None,
    profile: str = "merge",
) -> str:
    variant = _norm_mode(mode)
    if variant == "full":
        text = render_folder_summary_full(folder_abs, summary_text, profile=profile)
        return f"FOLDER SUMMARY (FULL)\n{text}".strip() + "\n"
    text = render_folder_summary_quick(folder_abs, summary_text, profile=profile)
    return f"FOLDER SUMMARY (QUICK)\n{text}".strip() + "\n"


def render_repo_summary(
    summary_text: str,
    mode: str | None = None,
    profile: str = "merge",
) -> str:
    variant = _norm_mode(mode)
    if variant == "full":
        text = render_repo_summary_full(summary_text, profile=profile)
        return f"REPOSITORY SUMMARY (FULL)\n{text}".strip() + "\n"
    text = render_repo_summary_quick(summary_text, profile=profile)
    return f"REPOSITORY SUMMARY (QUICK)\n{text}".strip() + "\n"


__all__ = [
    "ensure_folder_summary",
    "ensure_repo_summary",
    "render_folder_summary",
    "render_repo_summary",
    "get_full_coverage_proof",
]
