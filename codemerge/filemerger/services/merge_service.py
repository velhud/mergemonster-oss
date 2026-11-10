from __future__ import annotations

import os
from pathlib import Path
import shutil
from io import StringIO
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .. import FileProcessor
from ..api import _require_ensure_summaries
from ...models import FileMode
from ...llm.summarize import fetch_ai_summaries
from .dto import ProgressCb, UiState


def _mode_to_content_str(mode: FileMode) -> str:
    if mode is FileMode.EXCLUDE:
        return "exclude"
    if mode is FileMode.CODE:
        return "code"
    if mode in (FileMode.SKELETON, FileMode.API_SURFACE, FileMode.SIGNATURES):
        return "skeleton"
    if mode is FileMode.CODE_PLUS_SKELETON:
        return "code_plus_skeleton"
    if mode is FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON:
        return "code_plus_summary_short_plus_skeleton"
    if mode is FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON:
        return "code_plus_summary_long_plus_skeleton"
    if mode is FileMode.SUMMARY_SHORT:
        return "summary_short"
    if mode is FileMode.SUMMARY_LONG:
        return "summary_long"
    if mode is FileMode.SUMMARY_SHORT_PLUS_SKELETON:
        return "summary_short_plus_skeleton"
    if mode is FileMode.SUMMARY_LONG_PLUS_SKELETON:
        return "summary_long_plus_skeleton"
    if mode is FileMode.CODE_PLUS_SHORT:
        return "code_plus_short"
    if mode is FileMode.CODE_PLUS_LONG:
        return "code_plus_long"
    return "code"


def _filter_files_for_plan(files: List[str], plan: Optional[Dict[str, FileMode]]) -> List[str]:
    if not plan:
        return files
    filtered: List[str] = []
    for path in files:
        abs_path = os.path.abspath(path)
        mode = plan.get(path) or plan.get(abs_path)
        if isinstance(mode, FileMode):
            if mode is FileMode.EXCLUDE:
                continue
        else:
            key = str(mode or "").strip().upper()
            if key == "EXCLUDE":
                continue
        filtered.append(path)
    return filtered


def _apply_vendored_code_policy(
    fp: FileProcessor,
    root_dir: str,
    content_map: Dict[str, str],
    *,
    summarize_vendored: bool,
) -> Dict[str, str]:
    if summarize_vendored or not content_map:
        return content_map
    summary_modes = {
        "summary_short",
        "summary_long",
        "code_plus_short",
        "code_plus_long",
        "code_plus_summary_short_plus_skeleton",
        "code_plus_summary_long_plus_skeleton",
        "summary_short_plus_skeleton",
        "summary_long_plus_skeleton",
    }
    rel_map: Dict[str, str] = {}
    rels: List[str] = []
    for abs_path, mode in content_map.items():
        if mode not in summary_modes:
            continue
        try:
            rel = os.path.relpath(abs_path, root_dir)
        except Exception:
            continue
        rels.append(rel)
        rel_map[rel] = abs_path
    if not rels:
        return content_map
    vendored = fp.git_vendored_set(root_dir, rels)
    if not vendored:
        return content_map
    for rel in vendored:
        abs_path = rel_map.get(rel)
        if abs_path:
            if content_map.get(abs_path) in {"summary_short_plus_skeleton", "summary_long_plus_skeleton"}:
                content_map[abs_path] = "skeleton"
            else:
                content_map[abs_path] = "code"
    return content_map


def _progress_counts_adapter(progress_cb: Optional[ProgressCb], verb: str) -> Optional[Callable[[int, int], None]]:
    if progress_cb is None:
        return None

    def _inner(done: int, total: int) -> None:
        pct = (done / total * 100.0) if total else 0.0
        label = verb
        if total:
            label = f"{verb} {done}/{total}"
        progress_cb(pct, label)

    return _inner


def preview_merge_text(
    fp: FileProcessor,
    ui: UiState,
    files_abs: List[str],
    *,
    plan: Optional[Dict[str, FileMode]] = None,
    app_config: Optional[Dict] = None,
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    strict_summary_required: bool | None = None,
    virtual_blocks: Optional[List[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
) -> str:
    """Return merged preview text for the selected files."""

    cfg_local = dict(app_config or {})
    strict_required = (
        bool(cfg_local.get("strict_summary_required", False))
        if strict_summary_required is None
        else bool(strict_summary_required)
    )
    fallback_on_error = bool(cfg_local.get("fallback_to_code_on_summary_error", False))

    buf = StringIO()
    if plan:
        result = fp.merge_with_plan(
            directory=str(ui.root_dir),
            plan=plan,
            output_file=os.devnull,
            progress_callback=None,
            include_file_tree=ui.output.include_tree,
            create_json_manifest=False,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            summaries_by_file=None,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            strict_summary_required=strict_required,
            prompt_template=ui.summary.prompt_template,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            why_by_file=None,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            app_config=app_config,
            output_stream=buf,
            virtual_blocks=virtual_blocks,
            summarized_folder_rel_paths=summarized_folder_rel_paths,
            pinned_file_rel_paths=pinned_file_rel_paths,
        )
        if not result.ok:
            raise RuntimeError("merge_with_plan failed to generate preview")
    else:
        summaries_by_file, files_with_errors, summary_error = _maybe_fetch_summaries(
            fp,
            ui,
            files_abs,
            app_config,
            progress_callback=None,
            force_refresh=False,
        )
        if summary_error and strict_required:
            raise RuntimeError(summary_error)
        fp.merge_files(
            directory=str(ui.root_dir),
            files_to_merge=files_abs,
            output_file="",
            progress_callback=None,
            include_file_tree=ui.output.include_tree,
            include_summaries=False,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            create_json_manifest=False,
            summaries_by_file=summaries_by_file,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            content_mode=ui.summary.content_mode,
            files_with_summary_errors=files_with_errors,
            prompt_template=ui.summary.prompt_template,
            output_stream=buf,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            strict_summary_required=strict_required,
            fallback_to_code_on_summary_error=fallback_on_error,
            virtual_blocks=virtual_blocks,
            summarized_folder_rel_paths=summarized_folder_rel_paths,
            pinned_file_rel_paths=pinned_file_rel_paths,
        )
    return buf.getvalue()


def read_all_txt(folder: str) -> str:
    """Concatenate the contents of .txt files within *folder* for clipboard use."""

    parts: List[str] = []
    try:
        names = sorted(os.listdir(folder))
    except Exception:
        return ""

    for name in names:
        if not name.endswith(".txt") or name.startswith("multi-merge-overview"):
            continue
        path = Path(folder) / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                parts.append(handle.read())
        except Exception:
            continue
    return "\n\n=== END OF CHUNK ===\n\n".join(parts)


def _maybe_fetch_summaries(
    fp: FileProcessor,
    ui: UiState,
    files_abs: List[str],
    app_config: Optional[Dict],
    progress_callback: Optional[ProgressCb],
    *,
    plan: Optional[Dict[str, FileMode]] = None,
    force_refresh: bool = False,
) -> Tuple[Dict[str, Optional[str]], List[str], Optional[str]]:
    cfg = dict(app_config or {})
    content_mode = ui.summary.content_mode
    strict_required = bool(cfg.get("strict_summary_required", False))

    if plan:
        ensure = _require_ensure_summaries()
        root_abs = os.path.abspath(str(ui.root_dir))
        norm_plan, _ = fp._normalise_plan(plan, root_abs)

        short_targets: List[str] = []
        long_targets: List[str] = []
        for abs_path, mode in norm_plan.items():
            if mode in (
                FileMode.SUMMARY_SHORT,
                FileMode.CODE_PLUS_SHORT,
                FileMode.SUMMARY_SHORT_PLUS_SKELETON,
                FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
            ):
                short_targets.append(abs_path)
            if mode in (
                FileMode.SUMMARY_LONG,
                FileMode.CODE_PLUS_LONG,
                FileMode.SUMMARY_LONG_PLUS_SKELETON,
                FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
            ):
                long_targets.append(abs_path)

        summarize_vendored = bool(cfg.get("summarize_vendored", False))
        if not summarize_vendored and (short_targets or long_targets):
            rel_map: Dict[str, str] = {}
            for abs_path in set(short_targets + long_targets):
                try:
                    rel_map[abs_path] = os.path.relpath(abs_path, root_abs)
                except Exception:
                    continue
            rels = [rel_map[p] for p in short_targets + long_targets if p in rel_map]
            if rels:
                vendored = fp.git_vendored_set(root_abs, rels)
                short_targets = [p for p in short_targets if rel_map.get(p) not in vendored]
                long_targets = [p for p in long_targets if rel_map.get(p) not in vendored]

        summaries: Dict[str, Optional[str]] = {}
        errors: List[str] = []
        progress_counts = _progress_counts_adapter(progress_callback, "Summarizing")

        if short_targets:
            try:
                fetched, err = ensure(
                    short_targets,
                    "short",
                    fp,
                    cfg,
                    progress_counts,
                    force=force_refresh,
                    root=root_abs,
                    strict_required=strict_required,
                )
            except TypeError:
                fetched, err = ensure(
                    short_targets, "short", fp, cfg, progress_counts
                )
            for path, items in fetched.items():
                abs_path = os.path.abspath(path)
                summaries[path] = items
                summaries[abs_path] = items
            errors.extend(err)

        if long_targets:
            try:
                fetched, err = ensure(
                    long_targets,
                    "long",
                    fp,
                    cfg,
                    progress_counts,
                    force=force_refresh,
                    root=root_abs,
                    strict_required=strict_required,
                )
            except TypeError:
                fetched, err = ensure(
                    long_targets, "long", fp, cfg, progress_counts
                )
            for path, items in fetched.items():
                abs_path = os.path.abspath(path)
                summaries[path] = items
                summaries[abs_path] = items
            errors.extend(err)

        return summaries, errors, None
    need_summaries = content_mode in (
        "code_plus_short",
        "code_plus_long",
        "short_only",
        "long_only",
        "short_plus_skeleton",
        "long_plus_skeleton",
        "code_plus_short_plus_skeleton",
        "code_plus_long_plus_skeleton",
    )
    if not need_summaries:
        return {}, [], None

    def _summary_progress(completed: int, total: int) -> None:
        if progress_callback:
            pct = (completed / total * 100.0) if total else 0.0
            progress_callback(pct, f"Summarizing {completed}/{total} files...")

    try:
        summaries_by_file, files_with_errors = fetch_ai_summaries(
            files_abs,
            fp,
            content_mode,
            max_workers=ui.summary.groq_workers or 0,
            app_config=app_config,
            use_batch=ui.summary.use_batch,
            progress_callback=_summary_progress if progress_callback else None,
        )
        return summaries_by_file, files_with_errors, None
    except Exception as exc:
        return {}, [], str(exc)


def run_merge_single(
    fp: FileProcessor,
    ui: UiState,
    files_abs: List[str],
    out_path: str,
    progress: Optional[ProgressCb],
    *,
    app_config: Optional[Dict] = None,
    plan: Optional[Dict[str, FileMode]] = None,
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    strict_summary_required: bool | None = None,
    virtual_blocks: Optional[List[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
) -> Tuple[List[str], Optional[str]]:
    """Merge files into a single output and return summary warnings."""

    cfg_local = dict(app_config or {})
    strict_required = (
        bool(cfg_local.get("strict_summary_required", False))
        if strict_summary_required is None
        else bool(strict_summary_required)
    )
    fallback_on_error = bool(cfg_local.get("fallback_to_code_on_summary_error", False))

    if plan:
        result = fp.merge_with_plan(
            directory=str(ui.root_dir),
            plan=plan,
            output_file=out_path,
            progress_callback=_progress_counts_adapter(progress, "Merging"),
            include_file_tree=ui.output.include_tree,
            create_json_manifest=ui.output.json_manifest,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            summaries_by_file=None,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            strict_summary_required=strict_required,
            prompt_template=ui.summary.prompt_template,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            why_by_file=None,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            app_config=app_config,
            virtual_blocks=virtual_blocks,
            summarized_folder_rel_paths=summarized_folder_rel_paths,
            pinned_file_rel_paths=pinned_file_rel_paths,
        )
        return result.files_with_summary_errors, None

    summaries_by_file, files_with_errors, summary_error = _maybe_fetch_summaries(
        fp,
        ui,
        files_abs,
        app_config,
        progress,
        force_refresh=True,
    )
    if summary_error and strict_required:
        raise RuntimeError(summary_error)

    fp.merge_files(
        directory=str(ui.root_dir),
        files_to_merge=files_abs,
        output_file=out_path,
        progress_callback=progress,
        include_file_tree=ui.output.include_tree,
        include_summaries=False,
        custom_start_label=ui.output.custom_start,
        custom_end_label=ui.output.custom_end,
        file_tree_start_label=ui.output.tree_start,
        file_tree_end_label=ui.output.tree_end,
        create_json_manifest=ui.output.json_manifest,
        summaries_by_file=summaries_by_file,
        summaries_short=summaries_short,
        summaries_long=summaries_long,
        content_mode=ui.summary.content_mode,
        files_with_summary_errors=files_with_errors,
        prompt_template=ui.summary.prompt_template,
        remove_empty=ui.filters.remove_empty,
        enumerate_lines=ui.filters.enumerate_lines,
        markdown_fences=ui.output.markdown_fences,
        markdown_toc=ui.output.markdown_toc,
        strict_summary_required=strict_required,
        fallback_to_code_on_summary_error=fallback_on_error,
        virtual_blocks=virtual_blocks,
        summarized_folder_rel_paths=summarized_folder_rel_paths,
        pinned_file_rel_paths=pinned_file_rel_paths,
    )

    return files_with_errors, summary_error


def run_merge_split_lines(
    fp: FileProcessor,
    ui: UiState,
    files_abs: List[str],
    out_folder: str,
    num_chunks: int,
    progress: Optional[ProgressCb],
    *,
    app_config: Optional[Dict] = None,
    overwrite: bool = True,
    plan: Optional[Dict[str, FileMode]] = None,
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    strict_summary_required: bool | None = None,
    virtual_blocks: Optional[List[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
) -> Tuple[List[str], Optional[str]]:
    """Merge files into *num_chunks* text files inside *out_folder*."""

    if overwrite and os.path.exists(out_folder):
        shutil.rmtree(out_folder)

    cfg_local = dict(app_config or {})
    fallback_on_error = bool(cfg_local.get("fallback_to_code_on_summary_error", False))
    strict_required = (
        bool(cfg_local.get("strict_summary_required", False))
        if strict_summary_required is None
        else bool(strict_summary_required)
    )

    if plan:
        content_map = {path: _mode_to_content_str(mode) for path, mode in plan.items()}
        files_for_merge = _filter_files_for_plan(files_abs, plan)
        summaries_by_file, files_with_errors, _ = _maybe_fetch_summaries(
            fp,
            ui,
            files_abs,
            app_config,
            progress,
            plan=plan,
            force_refresh=True,
        )
        summarize_vendored = bool(cfg_local.get("summarize_vendored", False))
        content_map = _apply_vendored_code_policy(
            fp,
            str(ui.root_dir),
            content_map,
            summarize_vendored=summarize_vendored,
        )
        fp.merge_files_into_multiple(
            directory=str(ui.root_dir),
            files_to_merge=files_for_merge,
            output_folder=out_folder,
            num_text_files=num_chunks,
            progress_callback=progress,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            include_file_tree=ui.output.include_tree,
            create_json_manifest=ui.output.json_manifest,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            summaries_by_file=summaries_by_file,
            content_mode=ui.summary.content_mode,
            files_with_summary_errors=files_with_errors,
            prompt_template=ui.summary.prompt_template,
            verb="Splitting",
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            content_mode_map=content_map,
            fallback_to_code_on_summary_error=fallback_on_error,
            strict_summary_required=strict_required,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            virtual_blocks=virtual_blocks,
            summarized_folder_rel_paths=summarized_folder_rel_paths,
            pinned_file_rel_paths=pinned_file_rel_paths,
        )
        return files_with_errors, None

    summaries_by_file, files_with_errors, summary_error = _maybe_fetch_summaries(
        fp,
        ui,
        files_abs,
        app_config,
        progress,
        force_refresh=True,
    )
    if summary_error and strict_required:
        raise RuntimeError(summary_error)

    fp.merge_files_into_multiple(
        directory=str(ui.root_dir),
        files_to_merge=files_abs,
        output_folder=out_folder,
        num_text_files=num_chunks,
        progress_callback=progress,
        custom_start_label=ui.output.custom_start,
        custom_end_label=ui.output.custom_end,
        file_tree_start_label=ui.output.tree_start,
        file_tree_end_label=ui.output.tree_end,
        include_file_tree=ui.output.include_tree,
        create_json_manifest=ui.output.json_manifest,
        markdown_fences=ui.output.markdown_fences,
        markdown_toc=ui.output.markdown_toc,
        summaries_by_file=summaries_by_file,
        summaries_short=summaries_short,
        summaries_long=summaries_long,
        content_mode=ui.summary.content_mode,
        files_with_summary_errors=files_with_errors,
        prompt_template=ui.summary.prompt_template,
        verb="Splitting",
        remove_empty=ui.filters.remove_empty,
        enumerate_lines=ui.filters.enumerate_lines,
        fallback_to_code_on_summary_error=fallback_on_error,
        strict_summary_required=strict_required,
        virtual_blocks=virtual_blocks,
        summarized_folder_rel_paths=summarized_folder_rel_paths,
        pinned_file_rel_paths=pinned_file_rel_paths,
    )

    return files_with_errors, summary_error


def run_merge_split_tokens(
    fp: FileProcessor,
    ui: UiState,
    files_abs: List[str],
    out_folder: str,
    token_limit: int,
    progress: Optional[ProgressCb],
    *,
    overwrite: bool = True,
    app_config: Optional[Dict] = None,
    plan: Optional[Dict[str, FileMode]] = None,
    summaries_short: Optional[Dict[str, Optional[str]]] = None,
    summaries_long: Optional[Dict[str, Optional[str]]] = None,
    strict_summary_required: bool | None = None,
    virtual_blocks: Optional[List[Dict[str, str]]] = None,
    summarized_folder_rel_paths: Sequence[str] | None = None,
    pinned_file_rel_paths: Sequence[str] | None = None,
) -> List[str]:
    """Split merged output into token-bounded chunks under *token_limit*."""

    if overwrite and os.path.exists(out_folder):
        shutil.rmtree(out_folder)

    content_map = (
        {path: _mode_to_content_str(mode) for path, mode in plan.items()}
        if plan
        else None
    )
    files_for_merge = _filter_files_for_plan(files_abs, plan)

    cfg_local = dict(app_config or {})
    fallback_on_error = bool(cfg_local.get("fallback_to_code_on_summary_error", False))
    strict_required = (
        bool(cfg_local.get("strict_summary_required", False))
        if strict_summary_required is None
        else bool(strict_summary_required)
    )

    summaries_by_file, files_with_errors, summary_error = _maybe_fetch_summaries(
        fp,
        ui,
        files_abs,
        app_config,
        progress,
        plan=plan,
        force_refresh=True,
    )
    if summary_error and strict_required:
        raise RuntimeError(summary_error)
    if content_map:
        summarize_vendored = bool(cfg_local.get("summarize_vendored", False))
        content_map = _apply_vendored_code_policy(
            fp,
            str(ui.root_dir),
            content_map,
            summarize_vendored=summarize_vendored,
        )

    fp.merge_files_token_split(
        directory=str(ui.root_dir),
        files_to_merge=files_for_merge,
        output_folder=out_folder,
        token_limit=token_limit,
        progress_callback=progress,
        custom_start_label=ui.output.custom_start,
        custom_end_label=ui.output.custom_end,
        file_tree_start_label=ui.output.tree_start,
        file_tree_end_label=ui.output.tree_end,
        include_file_tree=ui.output.include_tree,
        create_json_manifest=ui.output.json_manifest,
        markdown_fences=ui.output.markdown_fences,
        markdown_toc=ui.output.markdown_toc,
        summaries_by_file=summaries_by_file,
        summaries_short=summaries_short,
        summaries_long=summaries_long,
        content_mode=ui.summary.content_mode,
        files_with_summary_errors=files_with_errors,
        prompt_template=ui.summary.prompt_template,
        verb="Splitting",
        remove_empty=ui.filters.remove_empty,
        enumerate_lines=ui.filters.enumerate_lines,
        why_by_file=None,
        content_mode_map=content_map,
        fallback_to_code_on_summary_error=fallback_on_error,
        strict_summary_required=strict_required,
        virtual_blocks=virtual_blocks,
        summarized_folder_rel_paths=summarized_folder_rel_paths,
        pinned_file_rel_paths=pinned_file_rel_paths,
    )
    return files_with_errors
