from __future__ import annotations

import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Sequence, TextIO

from .estimate import split_prompt_template
from .output import atomic_text_output
from .render import (
    check_strict_summary,
    decide_render,
    PORTABLE_ROOT_HEADER,
    escape_file_display,
    format_label,
    normalize_mode,
    summary_flags,
)


class MergeHost(Protocol):
    def generate_file_tree(
        self,
        directory: str,
        files: Sequence[str],
        ascii_mode: bool | None = None,
        summarized_folders: Sequence[str] | None = None,
        pinned_files: Sequence[str] | None = None,
    ) -> str: ...

    def transform_content(
        self, text: str, *, enumerate_lines: bool = False, remove_empty: bool = False
    ) -> str: ...

    def read_file_content_ex(self, file_path: str) -> tuple[Optional[str], str]: ...

    def file_token_count(self, file_path: str) -> int | None: ...

    def render_skeleton_block(
        self,
        file_path: str,
        *,
        root: str | None = None,
        enumerate_lines: bool = False,
        remove_empty: bool = False,
    ) -> str: ...

    def _commit_pending(self) -> None: ...


def merge_files(
    host: MergeHost,
    *,
    directory: str,
    files_to_merge: Sequence[str],
    output_file: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    include_file_tree: bool = True,
    include_summaries: bool = False,
    custom_start_label: str = "======= START OF FILE: {file_display} =======",
    custom_end_label: str = "======= END OF FILE: {file_display} =======",
    file_tree_start_label: str = "======= FILE TREE =======",
    file_tree_end_label: str = "======= END OF FILE TREE =======",
    create_json_manifest: bool = False,
    summaries_by_file: Optional[Dict[str, Optional[str]]] = None,
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    strict_summary_required: bool = False,
    content_mode: str = "code_only",
    files_with_summary_errors: Optional[Sequence[str]] = None,
    content_mode_map: Dict[str, str] | None = None,  # "exclude" | "code" | "code_plus_short" | "code_plus_long" | "summary_short" | "summary_long"
    fallback_to_code_on_summary_error: bool = True,
    prompt_template: str | None = None,
    output_stream: TextIO | None = None,
    overwrite: bool = True,
    remove_empty: bool = False,
    enumerate_lines: bool = False,
    why_by_file: Optional[Dict[str, str]] = None,
    markdown_fences: bool = False,
    markdown_toc: bool = False,
    format_summary: Callable[[str], str] | None = None,
    virtual_blocks: Optional[List[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
) -> bool:
    """Merge ``files_to_merge`` into ``output_file`` using ``host`` helpers."""

    formatter = format_summary or (lambda summary_text: str(summary_text or "").strip())

    try:
        manifest_data = {"root_folder": ".", "merged_files": []}

        prefix, suffix = split_prompt_template(prompt_template)

        output = atomic_text_output(output_file, overwrite=overwrite) if output_stream is None else nullcontext(output_stream)
        with output as outfile_obj:
            if prefix:
                outfile_obj.write(prefix.rstrip() + "\n\n")

            root_abs = os.path.abspath(directory)
            outfile_obj.write(PORTABLE_ROOT_HEADER)

            def _write_virtual_block(block: Dict[str, str]) -> None:
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
                    return
                outfile_obj.write(f"{start_label}\n")
                if text:
                    outfile_obj.write(text.rstrip() + "\n")
                outfile_obj.write(f"{end_label}\n\n")

            errors_set: set[str] = set()
            if files_with_summary_errors:
                for err_path in files_with_summary_errors:
                    errors_set.add(err_path)
                    try:
                        errors_set.add(os.path.abspath(err_path))
                    except Exception:
                        continue

            if virtual_blocks:
                for block in virtual_blocks:
                    if isinstance(block, dict):
                        _write_virtual_block(block)

            if include_file_tree:
                file_tree = host.generate_file_tree(
                    directory,
                    files_to_merge,
                    summarized_folders=summarized_folder_rel_paths,
                    pinned_files=pinned_file_rel_paths,
                )
                outfile_obj.write(f"{file_tree_start_label}\n")
                outfile_obj.write(file_tree)
                outfile_obj.write(f"\n{file_tree_end_label}\n\n")

            if markdown_toc:
                outfile_obj.write("## Table of Contents\n\n")
                for path in files_to_merge:
                    rel_path = os.path.relpath(path, start=directory)
                    anchor = re.sub(r"[^a-z0-9]+", "-", rel_path.lower()).strip("-")
                    outfile_obj.write(f"- [{rel_path}](#{anchor})\n")
                outfile_obj.write("\n")

            total_files = len(files_to_merge)

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

            for index, path in enumerate(files_to_merge):
                rel_path = os.path.relpath(path, start=directory)
                abs_path = os.path.join(root_abs, rel_path)
                file_display = f"'{escape_file_display(Path(rel_path).as_posix())}'"

                eff_mode = None
                if content_mode_map:
                    eff_mode = content_mode_map.get(abs_path) or content_mode_map.get(path)

                if eff_mode and eff_mode.lower() == "exclude":
                    if progress_callback:
                        progress = (index + 1) / total_files * 100
                        progress_callback(
                            progress,
                            f"Skipping {index + 1}/{total_files}: {os.path.basename(path)}",
                        )
                    continue

                start_label = format_label(custom_start_label, file_display)
                if markdown_fences or markdown_toc:
                    rel_md = rel_path.replace("\\", "/")
                    outfile_obj.write(f"## {rel_md}\n\n")
                    outfile_obj.write(f"{start_label}\n")
                else:
                    outfile_obj.write(f"{start_label}\n")

                if why_by_file:
                    reason = why_by_file.get(path) or why_by_file.get(abs_path)
                    if reason:
                        outfile_obj.write(f"# why: {reason}\n")

                mode_norm = normalize_mode(eff_mode)
                summary_kind, summary_required, requested_code_plus = summary_flags(
                    mode_norm, content_mode
                )

                summary_items: Optional[str] = None
                if summary_kind == "short":
                    summary_items = _lookup_summary(summary_lookup_short, path, abs_path)
                elif summary_kind == "long":
                    summary_items = _lookup_summary(summary_lookup_long, path, abs_path)

                summary_error = (path in errors_set) or (abs_path in errors_set)
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

                if write_summary:
                    if summary_items:
                        summary_text = formatter(summary_items)
                        for line in summary_text.splitlines():
                            outfile_obj.write(f"# {line}\n")
                    else:
                        outfile_obj.write("# Summary was unavailable.\n")
                    outfile_obj.write("\n")

                if write_skeleton:
                    skeleton_text = host.render_skeleton_block(
                        path,
                        root=directory,
                        enumerate_lines=enumerate_lines,
                        remove_empty=remove_empty,
                    )
                    if markdown_fences and skeleton_text:
                        lang = (Path(path).suffix.lstrip(".") or "text").lower()
                        outfile_obj.write(f"```{lang}\n")
                        outfile_obj.write(skeleton_text.rstrip())
                        outfile_obj.write("\n```\n\n")
                    else:
                        outfile_obj.write(skeleton_text)
                        outfile_obj.write("\n")

                if write_code:
                    if emit_summary_failed_comment:
                        outfile_obj.write("# SUMMARY FAILED – using code instead\n")
                    content, status = host.read_file_content_ex(path)
                    if content is None:
                        if status == "binary":
                            outfile_obj.write(
                                f"# [binary file omitted: {rel_path}]\n"
                            )
                        else:
                            outfile_obj.write(
                                f"# ERROR: Could not read file {rel_path}\n"
                            )
                    else:
                        transformed = host.transform_content(
                            content,
                            enumerate_lines=enumerate_lines,
                            remove_empty=remove_empty,
                        )
                        if markdown_fences and transformed:
                            lang = (Path(path).suffix.lstrip(".") or "text").lower()
                            outfile_obj.write(f"```{lang}\n")
                            outfile_obj.write(transformed)
                            outfile_obj.write("\n```\n")
                        else:
                            outfile_obj.write(transformed)

                end_label = format_label(custom_end_label, file_display)
                outfile_obj.write(f"\n{end_label}\n\n")

                if create_json_manifest:
                    manifest_data["merged_files"].append(
                        {"relative_path": Path(rel_path).as_posix()}
                    )

                if progress_callback:
                    progress = (index + 1) / total_files * 100
                    progress_callback(
                        progress,
                        f"Processing {index + 1}/{total_files}: {os.path.basename(path)}",
                    )

            if suffix:
                outfile_obj.write("\n" + suffix.lstrip())

        if create_json_manifest:
            manifest_path = output_file + ".manifest.json"
            with atomic_text_output(manifest_path, overwrite=overwrite) as manifest_file:
                json.dump(manifest_data, manifest_file, indent=2)

        host._commit_pending()
        return True
    except Exception as exc:
        raise RuntimeError(f"Error merging files: {exc}") from exc
