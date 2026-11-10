from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..api import _format_summary
from ..render import (
    check_strict_summary,
    decide_render,
    PORTABLE_ROOT_HEADER,
    escape_file_display,
    format_label,
    normalize_mode,
    summary_flags,
)
from ..types import FileProcessorLike as FileProcessor


def group_files_by_tokens(
    processor: "FileProcessor", files: List[str], token_limit: int
) -> List[List[str]]:
    """Greedy grouping using rendered token costs with default settings."""

    try:
        directory = os.path.commonpath(files) if files else os.getcwd()
    except Exception:
        directory = os.getcwd()
    if directory and os.path.isfile(directory):
        directory = os.path.dirname(directory)
    groups, _over_limit = _group_files_by_tokens(
        processor,
        directory=directory,
        files=files,
        token_limit=token_limit,
        custom_start_label="======= START OF FILE: {file_display} =======",
        custom_end_label="======= END OF FILE: {file_display} =======",
        file_tree_start_label="======= FILE TREE =======",
        file_tree_end_label="======= END OF FILE TREE =======",
        include_file_tree=False,
        markdown_fences=False,
        markdown_toc=False,
        summaries_by_file=None,
        content_mode="code_only",
        files_with_summary_errors=None,
        prompt_template=None,
        remove_empty=False,
        enumerate_lines=False,
        why_by_file=None,
        content_mode_map=None,
        fallback_to_code_on_summary_error=True,
        summaries_short=None,
        summaries_long=None,
        strict_summary_required=False,
        virtual_blocks=None,
        summarized_folder_rel_paths=None,
        pinned_file_rel_paths=None,
    )
    return groups


def _virtual_blocks_text(blocks: Optional[List[Dict[str, str]]]) -> str:
    if not blocks:
        return ""
    lines: List[str] = []
    for block in blocks:
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
        lines.append(start_label)
        if text:
            lines.append(text.rstrip())
        lines.append(end_label)
        lines.append("")
        lines.append("")
    return "\n".join(lines)


def _estimate_per_file_costs(
    processor: "FileProcessor",
    directory: str,
    files: List[str],
    *,
    custom_start_label: str,
    custom_end_label: str,
    file_tree_start_label: str,
    file_tree_end_label: str,
    markdown_fences: bool,
    markdown_toc: bool,
    summaries_by_file: Optional[Dict[str, Optional[str]]],
    content_mode: str,
    files_with_summary_errors: Optional[List[str]],
    remove_empty: bool,
    enumerate_lines: bool,
    why_by_file: Optional[Dict[str, str]],
    content_mode_map: Optional[Dict[str, str]],
    fallback_to_code_on_summary_error: bool,
    summaries_short: Optional[Dict[str, Optional[str]]],
    summaries_long: Optional[Dict[str, Optional[str]]],
    strict_summary_required: bool,
    virtual_blocks: Optional[List[Dict[str, str]]],
    summarized_folder_rel_paths: Sequence[str] | None,
    pinned_file_rel_paths: Sequence[str] | None,
) -> tuple[List[Dict[str, object]], int, int, int]:
    root_tokens = _count_tokens_safe(processor, PORTABLE_ROOT_HEADER)
    toc_header_tokens = 0
    toc_trailer_tokens = 0
    if markdown_toc:
        toc_header_tokens = _count_tokens_safe(processor, "## Table of Contents\n\n")
        toc_trailer_tokens = _count_tokens_safe(processor, "\n")

    entries: List[Dict[str, object]] = []
    for file_path in files:
        eff_mode = None
        if content_mode_map:
            try:
                abs_path = os.path.abspath(file_path)
            except Exception:
                abs_path = file_path
            eff_mode = content_mode_map.get(abs_path) or content_mode_map.get(file_path)
        if eff_mode and eff_mode.lower() == "exclude":
            continue
        total, _ = processor.merge_group_token_details(
            directory,
            [file_path],
            include_file_tree=False,
            custom_start_label=custom_start_label,
            custom_end_label=custom_end_label,
            file_tree_start_label=file_tree_start_label,
            file_tree_end_label=file_tree_end_label,
            prompt_template=None,
            remove_empty=remove_empty,
            enumerate_lines=enumerate_lines,
            markdown_fences=markdown_fences,
            markdown_toc=markdown_toc,
            content_mode=content_mode,
            summaries_by_file=summaries_by_file,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            content_mode_map=content_mode_map,
            files_with_summary_errors=files_with_summary_errors,
            fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
            why_by_file=why_by_file,
            strict_summary_required=strict_summary_required,
            virtual_blocks=None,
            summarized_folder_rel_paths=summarized_folder_rel_paths,
            pinned_file_rel_paths=pinned_file_rel_paths,
        )
        base_tokens = max(int(total - root_tokens), 0)
        toc_line_tokens = 0
        if markdown_toc:
            rel_path = os.path.relpath(file_path, start=directory)
            anchor = re.sub(r"[^a-z0-9]+", "-", rel_path.lower()).strip("-")
            toc_line_tokens = _count_tokens_safe(
                processor, f"- [{rel_path}](#{anchor})\n"
            )
            base_tokens = max(
                base_tokens - toc_header_tokens - toc_trailer_tokens - toc_line_tokens,
                0,
            )
        entries.append(
            {
                "path": file_path,
                "base_tokens": base_tokens,
                "toc_tokens": toc_line_tokens,
            }
        )
    return entries, root_tokens, toc_header_tokens, toc_trailer_tokens


def _group_files_by_tokens(
    processor: "FileProcessor",
    *,
    directory: str,
    files: List[str],
    token_limit: int,
    custom_start_label: str,
    custom_end_label: str,
    file_tree_start_label: str,
    file_tree_end_label: str,
    include_file_tree: bool,
    markdown_fences: bool,
    markdown_toc: bool,
    summaries_by_file: Optional[Dict[str, Optional[str]]],
    content_mode: str,
    files_with_summary_errors: Optional[List[str]],
    prompt_template: str | None,
    remove_empty: bool,
    enumerate_lines: bool,
    why_by_file: Optional[Dict[str, str]],
    content_mode_map: Optional[Dict[str, str]],
    fallback_to_code_on_summary_error: bool,
    summaries_short: Optional[Dict[str, Optional[str]]],
    summaries_long: Optional[Dict[str, Optional[str]]],
    strict_summary_required: bool,
    virtual_blocks: Optional[List[Dict[str, str]]],
    summarized_folder_rel_paths: Sequence[str] | None,
    pinned_file_rel_paths: Sequence[str] | None,
) -> tuple[List[List[str]], List[Dict[str, object]]]:
    prefix, suffix = "", ""
    if prompt_template and "{merged_content}" in prompt_template:
        prefix, suffix = prompt_template.split("{merged_content}", 1)

    entries, root_tokens, toc_header_tokens, toc_trailer_tokens = _estimate_per_file_costs(
        processor,
        directory,
        files,
        custom_start_label=custom_start_label,
        custom_end_label=custom_end_label,
        file_tree_start_label=file_tree_start_label,
        file_tree_end_label=file_tree_end_label,
        markdown_fences=markdown_fences,
        markdown_toc=markdown_toc,
        summaries_by_file=summaries_by_file,
        content_mode=content_mode,
        files_with_summary_errors=files_with_summary_errors,
        remove_empty=remove_empty,
        enumerate_lines=enumerate_lines,
        why_by_file=why_by_file,
        content_mode_map=content_mode_map,
        fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
        summaries_short=summaries_short,
        summaries_long=summaries_long,
        strict_summary_required=strict_summary_required,
        virtual_blocks=virtual_blocks,
        summarized_folder_rel_paths=summarized_folder_rel_paths,
        pinned_file_rel_paths=pinned_file_rel_paths,
    )

    prefix_tokens = (
        _count_tokens_safe(processor, prefix.rstrip() + "\n\n") if prefix else 0
    )
    suffix_tokens = (
        _count_tokens_safe(processor, "\n" + suffix.lstrip()) if suffix else 0
    )
    note_tokens = _count_tokens_safe(
        processor, "NOTE: Repository/folder summaries are in chunk 1.\n\n"
    )

    tree_tokens = 0
    if include_file_tree:
        try:
            tree_text = processor.generate_file_tree(
                directory,
                files,
                summarized_folders=summarized_folder_rel_paths,
                pinned_files=pinned_file_rel_paths,
            )
        except Exception:
            tree_text = ""
        tree_block = f"{file_tree_start_label}\n{tree_text}\n{file_tree_end_label}\n\n"
        tree_tokens = _count_tokens_safe(processor, tree_block)

    virtual_tokens = _count_tokens_safe(processor, _virtual_blocks_text(virtual_blocks))

    base_overhead = prefix_tokens + root_tokens + suffix_tokens
    first_overhead = base_overhead + virtual_tokens + tree_tokens
    if markdown_toc:
        first_overhead += toc_header_tokens + toc_trailer_tokens
    other_overhead = base_overhead + note_tokens

    groups: List[List[str]] = []
    over_limit: List[Dict[str, object]] = []
    current: List[str] = []
    current_tokens = 0
    for entry in entries:
        group_index = len(groups) + 1
        overhead = first_overhead if group_index == 1 else other_overhead
        toc_tokens = entry["toc_tokens"] if (markdown_toc and group_index == 1) else 0
        entry_tokens = int(entry["base_tokens"]) + int(toc_tokens)
        if current and overhead + current_tokens + entry_tokens > token_limit:
            groups.append(current)
            current = []
            current_tokens = 0
            group_index = len(groups) + 1
            overhead = first_overhead if group_index == 1 else other_overhead
            toc_tokens = entry["toc_tokens"] if (markdown_toc and group_index == 1) else 0
            entry_tokens = int(entry["base_tokens"]) + int(toc_tokens)
        if not current and overhead + entry_tokens > token_limit:
            over_limit.append(
                {
                    "path": str(entry["path"]),
                    "estimated_tokens": int(overhead + entry_tokens),
                }
            )
        current.append(str(entry["path"]))
        current_tokens += entry_tokens

    if current:
        groups.append(current)

    processor._commit_pending()
    return groups, over_limit


def _count_tokens_safe(processor: "FileProcessor", text: str) -> int:
    try:
        return int(processor.count_tokens(text))
    except Exception:
        return max(len(text) // 4, 1)


def merge_files_token_split(
    processor: "FileProcessor",
    directory: str,
    files_to_merge: List[str],
    output_folder: str,
    token_limit: int,
    progress_callback: Optional[Callable[[float, str], None]],
    custom_start_label: str,
    custom_end_label: str,
    file_tree_start_label: str,
    file_tree_end_label: str,
    include_file_tree: bool,
    create_json_manifest: bool,
    markdown_fences: bool = False,
    markdown_toc: bool = False,
    summaries_by_file: Optional[Dict[str, Optional[str]]] = None,
    content_mode: str = "code_only",
    files_with_summary_errors: Optional[List[str]] = None,
    prompt_template: str | None = None,
    verb: str = "Merging",
    remove_empty: bool = False,
    enumerate_lines: bool = False,
    why_by_file: Optional[Dict[str, str]] = None,
    content_mode_map: Optional[Dict[str, str]] = None,
    fallback_to_code_on_summary_error: bool = True,
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    strict_summary_required: bool = False,
    virtual_blocks: Optional[List[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
) -> None:
    """Merge files into multiple outputs ensuring each stays under a token limit."""

    prefix, suffix = "", ""
    if prompt_template and "{merged_content}" in prompt_template:
        prefix, suffix = prompt_template.split("{merged_content}", 1)

    def _resolved_mode(path: str) -> str | None:
        if not content_mode_map:
            return None
        try:
            abs_path = os.path.abspath(path)
        except Exception:
            abs_path = path
        return content_mode_map.get(abs_path) or content_mode_map.get(path)

    summary_lookup_short = dict(summaries_by_file or {})
    summary_lookup_long = dict(summaries_by_file or {})
    if summaries_short:
        summary_lookup_short.update(summaries_short)
    if summaries_long:
        summary_lookup_long.update(summaries_long)

    errors_set: set[str] = set()
    if files_with_summary_errors:
        for err_path in files_with_summary_errors:
            errors_set.add(err_path)
            try:
                errors_set.add(os.path.abspath(err_path))
            except Exception:
                continue

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

    note_text = "NOTE: Repository/folder summaries are in chunk 1.\n\n"
    groups, over_limit = _group_files_by_tokens(
        processor,
        directory=directory,
        files=files_to_merge,
        token_limit=token_limit,
        custom_start_label=custom_start_label,
        custom_end_label=custom_end_label,
        file_tree_start_label=file_tree_start_label,
        file_tree_end_label=file_tree_end_label,
        include_file_tree=include_file_tree,
        markdown_fences=markdown_fences,
        markdown_toc=markdown_toc,
        summaries_by_file=summaries_by_file,
        content_mode=content_mode,
        files_with_summary_errors=files_with_summary_errors,
        prompt_template=prompt_template,
        remove_empty=remove_empty,
        enumerate_lines=enumerate_lines,
        why_by_file=why_by_file,
        content_mode_map=content_mode_map,
        fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
        summaries_short=summaries_short,
        summaries_long=summaries_long,
        strict_summary_required=strict_summary_required,
        virtual_blocks=virtual_blocks,
        summarized_folder_rel_paths=summarized_folder_rel_paths,
        pinned_file_rel_paths=pinned_file_rel_paths,
    )

    tree_text = ""
    if include_file_tree:
        try:
            tree_text = processor.generate_file_tree(
                directory,
                files_to_merge,
                summarized_folders=summarized_folder_rel_paths,
                pinned_files=pinned_file_rel_paths,
            )
        except Exception:
            tree_text = ""
        tree_block = f"{file_tree_start_label}\n{tree_text}\n{file_tree_end_label}\n\n"
    else:
        tree_block = ""

    os.makedirs(output_folder, exist_ok=False)

    manifest_data = {"root_folder": ".", "groups": []}
    root_abs = os.path.abspath(directory)
    total_files_count = sum(len(group) for group in groups)
    files_merged_so_far = 0

    for group_index, group_files in enumerate(groups, start=1):
        out_filename = f"{group_index}.txt"

        final_path = os.path.join(output_folder, out_filename)
        manifest_entry = {"output_file": out_filename, "merged_files": []}

        with open(final_path, "w", encoding="utf-8", errors="replace") as out_file:
            if prefix:
                out_file.write(prefix.rstrip() + "\n\n")
            out_file.write(PORTABLE_ROOT_HEADER)

            if group_index > 1:
                out_file.write(note_text)
            else:
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
                        out_file.write(f"{start_label}\n")
                        if text:
                            out_file.write(text.rstrip() + "\n")
                        out_file.write(f"{end_label}\n\n")
                if include_file_tree and tree_block:
                    out_file.write(tree_block)

            if markdown_toc and group_index == 1:
                out_file.write("## Table of Contents\n\n")
                for path in group_files:
                    rel_path = os.path.relpath(path, start=directory)
                    anchor = re.sub(r"[^a-z0-9]+", "-", rel_path.lower()).strip("-")
                    out_file.write(f"- [{rel_path}](#{anchor})\n")
                out_file.write("\n")

            for file_path in list(group_files):
                rel_path = os.path.relpath(file_path, start=directory)
                abs_path = os.path.join(root_abs, rel_path)
                eff_mode = _resolved_mode(abs_path) or _resolved_mode(file_path)
                if eff_mode and eff_mode.lower() == "exclude":
                    continue

                file_display = f"'{escape_file_display(Path(rel_path).as_posix())}'"
                start_label = format_label(custom_start_label, file_display)
                if markdown_fences or markdown_toc:
                    rel_md = rel_path.replace("\\", "/")
                    out_file.write(f"## {rel_md}\n\n")
                out_file.write(f"{start_label}\n")
                if why_by_file:
                    reason = why_by_file.get(file_path) or why_by_file.get(abs_path)
                    if reason:
                        out_file.write(f"# why: {reason}\n")

                mode_norm = normalize_mode(eff_mode)
                summary_kind, summary_required, requested_code_plus = summary_flags(
                    mode_norm, content_mode
                )

                summary_items: Optional[str] = None
                if summary_kind == "short":
                    summary_items = _lookup_summary(summary_lookup_short, file_path, abs_path)
                elif summary_kind == "long":
                    summary_items = _lookup_summary(summary_lookup_long, file_path, abs_path)

                summary_error = file_path in errors_set or abs_path in errors_set
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
                summary_failed_comment = decision.emit_summary_failed_comment

                if write_summary:
                    if summary_items:
                        summary_text = _format_summary(summary_items)
                        for line in summary_text.splitlines():
                            out_file.write(f"# {line}\n")
                    else:
                        out_file.write("# Summary was unavailable.\n")
                    out_file.write("\n")

                if write_skeleton:
                    skeleton_text = processor.render_skeleton_block(
                        file_path,
                        root=directory,
                        enumerate_lines=enumerate_lines,
                        remove_empty=remove_empty,
                    )
                    if markdown_fences and skeleton_text:
                        lang = (Path(file_path).suffix.lstrip(".") or "text").lower()
                        out_file.write(f"```{lang}\n")
                        out_file.write(skeleton_text.rstrip())
                        out_file.write("\n```\n\n")
                    else:
                        out_file.write(skeleton_text)
                        out_file.write("\n")

                if write_code:
                    if summary_failed_comment:
                        out_file.write("# SUMMARY FAILED – using code instead\n")

                    content: Optional[str]
                    status: str
                    content, status = processor.read_file_content_ex(file_path)
                    if content is None:
                        if status == "binary":
                            out_file.write(
                                f"# [binary file omitted: {rel_path}]\n"
                            )
                        else:
                            out_file.write(
                                f"# ERROR: Could not read file {rel_path}\n"
                            )
                    else:
                        transformed = processor.transform_content(
                            content,
                            enumerate_lines=enumerate_lines,
                            remove_empty=remove_empty,
                        )
                        if markdown_fences and transformed:
                            lang = (Path(file_path).suffix.lstrip(".") or "text").lower()
                            out_file.write(f"```{lang}\n")
                            out_file.write(transformed)
                            out_file.write("\n```\n")
                        else:
                            out_file.write(transformed)

                end_label = format_label(custom_end_label, file_display)
                out_file.write(f"\n{end_label}\n\n")

                try:
                    _total_tokens, file_tokens = processor.merge_group_token_details(
                        directory,
                        [file_path],
                        include_file_tree=False,
                        custom_start_label=custom_start_label,
                        custom_end_label=custom_end_label,
                        file_tree_start_label=file_tree_start_label,
                        file_tree_end_label=file_tree_end_label,
                        prompt_template=None,
                        remove_empty=remove_empty,
                        enumerate_lines=enumerate_lines,
                        markdown_fences=markdown_fences,
                        markdown_toc=False,
                        content_mode=content_mode,
                        summaries_by_file=summaries_by_file,
                        summaries_short=summaries_short,
                        summaries_long=summaries_long,
                        content_mode_map=content_mode_map,
                        files_with_summary_errors=files_with_summary_errors,
                        fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
                        why_by_file=why_by_file,
                        strict_summary_required=strict_summary_required,
                        virtual_blocks=None,
                        summarized_folder_rel_paths=summarized_folder_rel_paths,
                        pinned_file_rel_paths=pinned_file_rel_paths,
                    )
                except Exception:
                    if enumerate_lines or remove_empty:
                        try:
                            file_tokens = processor.processed_token_count(
                                file_path,
                                enumerate_lines=enumerate_lines,
                                remove_empty=remove_empty,
                            )
                        except Exception:
                            file_tokens = processor.file_token_count(file_path)
                    else:
                        file_tokens = processor.file_token_count(file_path)
                manifest_entry["merged_files"].append(
                    {
                        "relative_path": Path(rel_path).as_posix(),
                        "token_count": file_tokens,
                    }
                )

                files_merged_so_far += 1
                if progress_callback and total_files_count:
                    percent = (files_merged_so_far / total_files_count) * 100
                    progress_callback(
                        percent,
                        f"{verb} file {files_merged_so_far}/{total_files_count}",
                    )

            if suffix:
                out_file.write("\n" + suffix.lstrip())

        manifest_data["groups"].append(manifest_entry)

    overview_file = os.path.join(output_folder, "multi-merge-overview.txt")
    with open(overview_file, "w", encoding="utf-8", errors="replace") as overview:
        if prefix:
            overview.write(prefix.rstrip() + "\n\n")
        overview.write(PORTABLE_ROOT_HEADER)

        overview.write(f"{file_tree_start_label}\n")
        overview.write(tree_text)
        overview.write(f"\n{file_tree_end_label}\n\n")

        if over_limit:
            overview.write(
                "WARNING: Some files exceed the token limit and were placed in their own chunk.\n"
            )
            for entry in over_limit:
                path = Path(os.path.relpath(str(entry["path"]), directory)).as_posix()
                overview.write(f"  - {path} (estimated tokens: {entry.get('estimated_tokens')})\n")
            overview.write("\n")

        overview.write(
            f"Files grouped into chunks with max {token_limit} tokens each:\n\n"
        )
        for group_index, group in enumerate(manifest_data["groups"], start=1):
            merged_files = group.get("merged_files", [])
            if not merged_files:
                overview.write(f"Chunk {group_index}: [no files]\n\n")
                continue

            if len(merged_files) == 1:
                info = merged_files[0]
                single_file = info["relative_path"]
                token_count = info.get("token_count")
                display_tokens = token_count if token_count is not None else "unreadable"
                base_name = group.get("output_file") or os.path.basename(single_file)
                overview.write(
                    f"Chunk {group_index}: 1 file => Output: {base_name}\n"
                )
                overview.write(
                    f"   * {single_file}  (tokens: {display_tokens})\n\n"
                )
            else:
                out_name = group.get("output_file", f"{group_index}.txt")
                total_tokens = sum((info.get("token_count") or 0) for info in merged_files)
                overview.write(
                    f"Chunk {group_index}: {len(merged_files)} files => Output: {out_name}  (total tokens: {total_tokens})\n"
                )
                for info in merged_files:
                    path = info.get("relative_path")
                    token_count = info.get("token_count")
                    display_tokens = token_count if token_count is not None else "unreadable"
                    overview.write(
                        f"   * {path}  (tokens: {display_tokens})\n"
                    )
                overview.write("\n")
        if suffix:
            overview.write("\n" + suffix.lstrip())

    if create_json_manifest:
        manifest_path = os.path.join(output_folder, "multi-merge.manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as manifest:
            json.dump(manifest_data, manifest, indent=2)

    processor._commit_pending()
