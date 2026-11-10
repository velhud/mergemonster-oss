from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

from .render import (
    check_strict_summary,
    decide_render,
    PORTABLE_ROOT_HEADER,
    escape_file_display,
    format_label,
    normalize_mode,
    summary_flags,
)



def transform_content(
    text: str,
    *,
    enumerate_lines: bool = False,
    remove_empty: bool = False,
) -> str:
    """Apply optional whitespace filtering and line enumeration."""

    lines = text.splitlines()
    if remove_empty:
        lines = [line for line in lines if line.strip()]
    if enumerate_lines:
        width = len(str(len(lines))) if lines else 1
        lines = [f"{index + 1:>{width}}: {line}" for index, line in enumerate(lines)]
    return "\n".join(lines)


def processed_token_count(
    read_file_content: Callable[[str], Optional[str]],
    count_tokens: Callable[[str], int],
    file_path: str,
    *,
    enumerate_lines: bool = False,
    remove_empty: bool = False,
) -> int:
    """Token count after applying optional line transformations."""

    content = read_file_content(file_path)
    if content is None:
        return 0
    transformed = transform_content(
        content,
        enumerate_lines=enumerate_lines,
        remove_empty=remove_empty,
    )
    return count_tokens(transformed)


def merge_file_tokens(
    file_token_count: Callable[[str], int | None],
    processed_counter: Callable[[str, bool, bool], int],
    file_path: str,
    *,
    enumerate_lines: bool,
    remove_empty: bool,
) -> int:
    if enumerate_lines or remove_empty:
        return processed_counter(
            file_path,
            enumerate_lines=enumerate_lines,
            remove_empty=remove_empty,
        )
    tokens = file_token_count(file_path)
    return int(tokens) if tokens is not None else 0


def split_prompt_template(prompt_template: str | None) -> Tuple[str, str]:
    if prompt_template and "{merged_content}" in prompt_template:
        try:
            return prompt_template.split("{merged_content}", 1)
        except Exception:
            return "", ""
    return "", ""


def merge_group_token_details(
    directory: str,
    files: Sequence[str],
    *,
    include_file_tree: bool,
    custom_start_label: str,
    custom_end_label: str,
    file_tree_start_label: str,
    file_tree_end_label: str,
    prompt_template: str | None,
    remove_empty: bool,
    enumerate_lines: bool,
    markdown_fences: bool,
    markdown_toc: bool,
    content_mode: str,
    summaries_by_file: Optional[Dict[str, Optional[str]]],
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    read_file_content_ex: Callable[[str], Tuple[Optional[str], str]],
    count_tokens: Callable[[str], int],
    generate_file_tree_fn: Callable[..., str],
    format_summary: Callable[[str], str],
    merge_file_tokens_fn: Callable[[str, bool, bool], int],
    render_skeleton_block_fn: Optional[Callable[[str, bool, bool], str]] = None,
    content_mode_map: Optional[Dict[str, str]] = None,  # "exclude"|"code"|"code_plus_short"|"code_plus_long"|"summary_short"|"summary_long"
    files_with_summary_errors: Optional[Sequence[str]] = None,
    fallback_to_code_on_summary_error: bool = True,
    why_by_file: Optional[Dict[str, str]] = None,
    strict_summary_required: bool = False,
    virtual_blocks: Optional[Sequence[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
) -> Tuple[int, int]:
    """Return (total_tokens_with_headers, content_only_tokens) for a merge group."""

    if not files:
        return 0, 0

    root_abs = os.path.abspath(directory)
    prefix, suffix = split_prompt_template(prompt_template)

    content_total = 0
    # Track per-file decisions to mirror writer behavior
    per_file_flags: Dict[str, Dict[str, bool]] = {}
    excluded: set[str] = set()
    errors_set = set(files_with_summary_errors or [])
    summary_lookup_short = dict(summaries_by_file or {})
    summary_lookup_long = dict(summaries_by_file or {})
    if summaries_short:
        summary_lookup_short.update(summaries_short)
    if summaries_long:
        summary_lookup_long.update(summaries_long)

    def _lookup_summary(
        table: Dict[str, Optional[str]] | None,
        key: str,
        abs_key: str,
    ) -> Optional[str]:
        if not table:
            return None
        if key in table:
            return table[key]
        if abs_key in table:
            return table[abs_key]
        return None

    for path in files:
        abs_path = os.path.abspath(path)
        emit_summary_failed_comment = False

        eff = (content_mode_map or {}).get(abs_path) or (content_mode_map or {}).get(path)
        if eff and eff.lower() == "exclude":
            excluded.add(path)
            per_file_flags[abs_path] = {
                "code": False,
                "summary": False,
                "comment": False,
            }
            continue
        if why_by_file:
            reason = why_by_file.get(path) or why_by_file.get(abs_path)
            if reason:
                content_total += count_tokens(f"# why: {reason}\n")
        mode_norm = normalize_mode(eff)
        summary_kind, summary_required, requested_code_plus = summary_flags(
            mode_norm, content_mode
        )

        summary_items: Optional[str] = None
        if summary_kind == "short":
            summary_items = _lookup_summary(summary_lookup_short, path, abs_path)
        elif summary_kind == "long":
            summary_items = _lookup_summary(summary_lookup_long, path, abs_path)

        summary_error = (path in errors_set or abs_path in errors_set)
        summary_available = summary_items is not None
        check_strict_summary(
            strict_summary_required=strict_summary_required,
            summary_required=summary_required,
            summary_available=summary_available,
            summary_error=summary_error,
            abs_path=abs_path,
        )

        decision = decide_render(
            mode_norm=mode_norm,
            content_mode=content_mode,
            summary_required=summary_required,
            requested_code_plus=requested_code_plus,
            summary_available=summary_available,
            summary_error=summary_error,
            fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
            strict_summary_required=strict_summary_required,
        )
        write_code = decision.write_code
        write_summary = decision.write_summary
        write_skeleton = decision.write_skeleton
        emit_summary_failed_comment = decision.emit_summary_failed_comment

        per_file_flags[abs_path] = {
            "code": write_code,
            "summary": write_summary,
            "skeleton": write_skeleton,
            "comment": emit_summary_failed_comment,
        }

        if write_summary and summary_items:
            summary_text = format_summary(summary_items)
            for line in summary_text.splitlines():
                content_total += count_tokens(f"# {line}\n")
            content_total += count_tokens("\n")
        if write_summary and not summary_items:
            content_total += count_tokens("# Summary was unavailable.\n")
            content_total += count_tokens("\n")

        if write_skeleton:
            if render_skeleton_block_fn is None:
                skeleton_text = (
                    "# SKELETON\n"
                    "# parser_status: unsupported\n\n"
                    "# Skeleton unavailable: estimator was not given a skeleton renderer.\n"
                )
            else:
                skeleton_text = render_skeleton_block_fn(
                    path,
                    enumerate_lines,
                    remove_empty,
                )
            content_total += count_tokens(skeleton_text)
            content_total += count_tokens("\n")

        if write_code:
            if emit_summary_failed_comment:
                content_total += count_tokens("# SUMMARY FAILED – using code instead\n")
            content, status = read_file_content_ex(path)
            if content is None:
                rel_path = os.path.relpath(path, start=directory)
                if status == "binary":
                    content_total += count_tokens(
                        f"# [binary file omitted: {rel_path}]\n"
                    )
                else:
                    content_total += count_tokens(
                        f"# ERROR: Could not read file {rel_path}\n"
                    )
            else:
                content_total += merge_file_tokens_fn(
                    path,
                    enumerate_lines=enumerate_lines,
                    remove_empty=remove_empty,
                )

    overhead_tokens = 0
    if prefix:
        overhead_tokens += count_tokens(prefix.rstrip() + "\n\n")

    overhead_tokens += count_tokens(PORTABLE_ROOT_HEADER)

    if virtual_blocks:
        for block in virtual_blocks:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("kind", "") or "").strip().lower()
            text = str(block.get("text", "") or "")
            display = str(block.get("display", "") or "")
            variant = str(block.get("variant", "") or "").strip().lower()
            suffix = ""
            if variant == "full":
                suffix = " (FULL)"
            elif variant == "quick":
                suffix = " (QUICK)"
            if kind == "repo":
                start_label = f"======= REPOSITORY SUMMARY{suffix} ======="
                end_label = f"======= END OF REPOSITORY SUMMARY{suffix} ======="
            elif kind == "folder":
                start_label = f"======= START OF FOLDER SUMMARY{suffix}: '{display}' ======="
                end_label = f"======= END OF FOLDER SUMMARY{suffix}: '{display}' ======="
            else:
                continue
            overhead_tokens += count_tokens(start_label + "\n")
            if text:
                content_total += count_tokens(text.rstrip() + "\n")
            overhead_tokens += count_tokens(end_label + "\n\n")

    if include_file_tree:
        try:
            tree_text = generate_file_tree_fn(
                directory,
                files,
                summarized_folders=summarized_folder_rel_paths,
                pinned_files=pinned_file_rel_paths,
            )
        except Exception:
            tree_text = ""
        overhead_tokens += count_tokens(f"{file_tree_start_label}\n")
        overhead_tokens += count_tokens(tree_text)
        overhead_tokens += count_tokens(f"\n{file_tree_end_label}\n\n")

    if markdown_toc:
        overhead_tokens += count_tokens("## Table of Contents\n\n")
        for path in files:  # writer includes all input files in TOC, even excluded
            rel_path = os.path.relpath(path, start=directory)
            anchor = re.sub(r"[^a-z0-9]+", "-", rel_path.lower()).strip("-")
            overhead_tokens += count_tokens(f"- [{rel_path}](#{anchor})\n")
        overhead_tokens += count_tokens("\n")

    for path in files:
        if path in excluded:
            continue
        rel_path = os.path.relpath(path, start=directory)
        abs_path = os.path.join(root_abs, rel_path)
        file_display = f"'{escape_file_display(Path(rel_path).as_posix())}'"
        start_label = format_label(custom_start_label, file_display)
        end_label = format_label(custom_end_label, file_display)

        if markdown_fences or markdown_toc:
            rel_md = rel_path.replace("\\", "/")
            overhead_tokens += count_tokens(f"## {rel_md}\n\n")

        overhead_tokens += count_tokens(start_label + "\n")

        flags = per_file_flags.get(abs_path) or per_file_flags.get(path) or {"code": False}
        if markdown_fences and (flags.get("code") or flags.get("skeleton")):
            content, status = read_file_content_ex(path)
            if content is not None or flags.get("skeleton"):
                lang = (Path(path).suffix.lstrip(".") or "text").lower()
                if flags.get("skeleton"):
                    skeleton_text = render_skeleton_block_fn(path, enumerate_lines, remove_empty)
                    if skeleton_text:
                        overhead_tokens += count_tokens(f"```{lang}\n")
                        overhead_tokens += count_tokens("\n```\n\n")
                if flags.get("code") and content is not None:
                    transformed = transform_content(
                        content,
                        enumerate_lines=enumerate_lines,
                        remove_empty=remove_empty,
                    )
                    if transformed:
                        overhead_tokens += count_tokens(f"```{lang}\n")
                        overhead_tokens += count_tokens("\n```\n")
            elif status == "binary":
                # binary files are represented as comments elsewhere; no fence tokens
                pass

        overhead_tokens += count_tokens("\n" + end_label + "\n\n")

    if suffix:
        overhead_tokens += count_tokens("\n" + suffix.lstrip())

    total = content_total + overhead_tokens
    return total, content_total


__all__ = [
    "merge_file_tokens",
    "merge_group_token_details",
    "processed_token_count",
    "split_prompt_template",
    "transform_content",
]
