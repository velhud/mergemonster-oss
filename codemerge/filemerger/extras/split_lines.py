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


def merge_files_into_multiple(
    processor: "FileProcessor",
    directory: str,
    files_to_merge: List[str],
    output_folder: str,
    num_text_files: int,
    progress_callback: Optional[Callable[[float, str], None]],
    custom_start_label: str,
    custom_end_label: str,
    file_tree_start_label: str,
    file_tree_end_label: str,
    include_file_tree: bool,
    create_json_manifest: bool,
    summaries_by_file: Optional[Dict[str, Optional[str]]],
    content_mode: str,
    files_with_summary_errors: Optional[List[str]] = None,
    prompt_template: str | None = None,
    verb: str = "Merging",
    remove_empty: bool = False,
    enumerate_lines: bool = False,
    content_mode_map: Optional[Dict[str, str]] = None,
    fallback_to_code_on_summary_error: bool = True,
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    strict_summary_required: bool = False,
    virtual_blocks: Optional[List[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
    markdown_fences: bool = False,
    markdown_toc: bool = False,
) -> None:
    """Split a merge across multiple files balanced by line counts."""

    prefix, suffix = "", ""
    if prompt_template and "{merged_content}" in prompt_template:
        prefix, suffix = prompt_template.split("{merged_content}", 1)

    root_abs = os.path.abspath(directory)

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

    def _rendered_line_count(path: str) -> int:
        eff_mode = _resolved_mode(path)
        if eff_mode and eff_mode.lower() == "exclude":
            return 0
        abs_path = os.path.abspath(path)
        mode_norm = normalize_mode(eff_mode)
        summary_kind, summary_required, requested_code_plus = summary_flags(
            mode_norm, content_mode
        )
        summary_items: Optional[str] = None
        if summary_kind == "short":
            summary_items = _lookup_summary(summary_lookup_short, path, abs_path)
        elif summary_kind == "long":
            summary_items = _lookup_summary(summary_lookup_long, path, abs_path)
        summary_error = path in errors_set or abs_path in errors_set
        decision = decide_render(
            mode_norm=mode_norm,
            content_mode=content_mode,
            summary_required=summary_required,
            requested_code_plus=requested_code_plus,
            summary_available=summary_items is not None,
            summary_error=summary_error,
            fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
            strict_summary_required=strict_summary_required,
        )
        count = 4
        if decision.write_summary:
            if summary_items:
                count += max(1, len(_format_summary(summary_items).splitlines()))
            else:
                count += 1
        if decision.write_skeleton:
            try:
                count += max(
                    1,
                    len(
                        processor.render_skeleton_block(
                            path,
                            root=directory,
                            enumerate_lines=enumerate_lines,
                            remove_empty=remove_empty,
                        ).splitlines()
                    ),
                )
            except Exception:
                count += 1
        if decision.write_code:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    count += sum(1 for _ in handle)
            except Exception:
                count += 1
        return count

    file_line_counts: List[tuple[str, int]] = []
    for path in files_to_merge:
        eff_mode = _resolved_mode(path)
        if eff_mode and eff_mode.lower() == "exclude":
            continue
        line_count = _rendered_line_count(path)
        file_line_counts.append((path, line_count))

    file_line_counts.sort(key=lambda item: item[1], reverse=True)

    bins: List[dict[str, object]] = []
    for file_path, line_count in file_line_counts:
        if len(bins) < num_text_files:
            bins.append({"files": [(file_path, line_count)], "sum": line_count})
            continue

        smallest = min(bins, key=lambda entry: entry["sum"])
        smallest["files"].append((file_path, line_count))
        smallest["sum"] = smallest["sum"] + line_count  # type: ignore[assignment]

    os.makedirs(output_folder, exist_ok=False)

    manifest_data = {"root_folder": ".", "groups": []}
    total_files_count = sum(
        1 for path in files_to_merge if not (_resolved_mode(path) or "").lower() == "exclude"
    )
    files_merged_so_far = 0

    def _write_virtual_blocks(blocks: List[Dict[str, str]]) -> str:
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
        return "\n".join(lines)

    for bin_index, bin_data in enumerate(bins, start=1):
        group_files: List[tuple[str, int]] = bin_data["files"]  # type: ignore[assignment]

        out_filename = f"{bin_index}.txt"

        final_path = os.path.join(output_folder, out_filename)
        group_manifest_entry = {"output_file": out_filename, "merged_files": []}

        with open(final_path, "w", encoding="utf-8", errors="replace") as out_file:
            if prefix:
                out_file.write(prefix.rstrip() + "\n\n")
            out_file.write(PORTABLE_ROOT_HEADER)

            if bin_index > 1:
                out_file.write("NOTE: Repository/folder summaries are in chunk 1.\n\n")
            else:
                if virtual_blocks:
                    out_file.write(_write_virtual_blocks(virtual_blocks))
                if include_file_tree:
                    file_tree = processor.generate_file_tree(
                        directory,
                        files_to_merge,
                        summarized_folders=summarized_folder_rel_paths,
                        pinned_files=pinned_file_rel_paths,
                    )
                    out_file.write(f"{file_tree_start_label}\n")
                    out_file.write(file_tree)
                    out_file.write(f"\n{file_tree_end_label}\n\n")

            if markdown_toc and bin_index == 1:
                out_file.write("## Table of Contents\n\n")
                for path, _ in group_files:
                    rel_path = os.path.relpath(path, start=directory)
                    anchor = re.sub(r"[^a-z0-9]+", "-", rel_path.lower()).strip("-")
                    out_file.write(f"- [{rel_path}](#{anchor})\n")
                out_file.write("\n")

            for file_path, _ in group_files:
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
                    content, status = processor.read_file_content_ex(file_path)
                    if content is None:
                        if status == "binary":
                            out_file.write(f"# [binary file omitted: {rel_path}]\n")
                        else:
                            out_file.write(f"# ERROR: Could not read file {rel_path}\n")
                    else:
                        content = processor.transform_content(
                            content,
                            enumerate_lines=enumerate_lines,
                            remove_empty=remove_empty,
                        )
                        if markdown_fences and content:
                            lang = (Path(file_path).suffix.lstrip(".") or "text").lower()
                            out_file.write(f"```{lang}\n")
                            out_file.write(content)
                            out_file.write("\n```\n")
                        else:
                            out_file.write(content)

                end_label = format_label(custom_end_label, file_display)
                out_file.write(f"\n{end_label}\n\n")

                group_manifest_entry["merged_files"].append(
                    {"relative_path": Path(rel_path).as_posix()}
                )

                files_merged_so_far += 1
                if progress_callback and total_files_count:
                    percent = (files_merged_so_far / total_files_count) * 100
                    progress_callback(
                        percent, f"{verb} file {files_merged_so_far}/{total_files_count}"
                    )

            if suffix:
                out_file.write("\n" + suffix.lstrip())

        manifest_data["groups"].append(group_manifest_entry)

    overview_file = os.path.join(output_folder, "multi-merge-overview.txt")
    with open(overview_file, "w", encoding="utf-8", errors="replace") as overview:
        if prefix:
            overview.write(prefix.rstrip() + "\n\n")
        overview.write(PORTABLE_ROOT_HEADER)

        if include_file_tree:
            file_tree = processor.generate_file_tree(
                directory,
                files_to_merge,
                summarized_folders=summarized_folder_rel_paths,
                pinned_files=pinned_file_rel_paths,
            )
            overview.write(f"{file_tree_start_label}\n")
            overview.write(file_tree)
            overview.write(f"\n{file_tree_end_label}\n\n")

        overview.write("Below is how files were grouped into the final merged chunks:\n\n")
        for bin_index, bin_data in enumerate(bins, start=1):
            group_files = bin_data["files"]  # type: ignore[assignment]
            if len(group_files) == 1:
                single_file, line_count = group_files[0]
                base_name = os.path.basename(single_file)
                if not base_name.lower().endswith(".txt"):
                    base_name += ".txt"
                overview.write(f"Chunk {bin_index}: 1 file => Output: {base_name}\n")
                overview.write(f"   * {Path(os.path.relpath(single_file, directory)).as_posix()}\n\n")
            else:
                out_name = f"{bin_index}.txt"
                overview.write(
                    f"Chunk {bin_index}: {len(group_files)} files => Output: {out_name}\n"
                )
                for path, line_count in group_files:
                    overview.write(f"   * {Path(os.path.relpath(path, directory)).as_posix()}  (lines: {line_count})\n")
                overview.write("\n")
        if suffix:
            overview.write("\n" + suffix.lstrip())

    if create_json_manifest:
        manifest_path = os.path.join(output_folder, "multi-merge.manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as manifest:
            json.dump(manifest_data, manifest, indent=2)

    processor._commit_pending()
