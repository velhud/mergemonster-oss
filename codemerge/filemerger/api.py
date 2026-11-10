from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, TextIO

from codemerge.core import classify as core_classify
from codemerge.core import io as core_io
from codemerge.core import token_cache as core_token_cache
from codemerge.core import tokens as core_tokens
from codemerge.logger import get_logger
from codemerge.metrics import EVENT as M_EVENT, emit as metric_emit
from codemerge.models import Category, ScanContext, FileMode
from .config import AppConfig

from . import estimate as estimate_mod
from . import git as git_mod
from . import merge_basic as merge_basic_mod
from . import scan as scan_mod

def _format_summary(summary_text: str) -> str:
    return str(summary_text or "").strip()


_ensure_summaries_cached: Optional[Callable[..., object]] = None


def _require_ensure_summaries() -> Callable[
    [...],
    tuple[Dict[str, Optional[str]], List[str]],
]:
    """Dynamically import and cache orchestrator.ensure_summaries."""

    global _ensure_summaries_cached
    if _ensure_summaries_cached is None:
        try:
            from codemerge.summarizer.shared.orchestrator import ensure_summaries as _ensure  # type: ignore
        except Exception as exc:  # pragma: no cover - orchestrator optional during early refactors
            raise RuntimeError(
                "summarizer.shared.orchestrator.ensure_summaries is unavailable"
            ) from exc
        _ensure_summaries_cached = _ensure
    return _ensure_summaries_cached  # type: ignore[return-value]


logger = get_logger(__name__)

def _normalize_exclusion_entry(entry: object) -> str:
    if entry is None:
        return ""
    try:
        raw = str(entry).strip()
    except Exception:
        return ""
    if not raw:
        return ""
    expanded = raw
    try:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if os.path.isabs(expanded):
            return os.path.normcase(os.path.abspath(expanded))
    except Exception:
        expanded = raw
    try:
        return os.path.normcase(os.path.normpath(expanded))
    except Exception:
        return raw


def _require_prune_utils(attr: str):
    try:
        from codemerge.prune import utils as prune_utils
    except ImportError as exc:
        raise RuntimeError(
            "Prune utilities are unavailable; include codemerge.prune when calling pruning helpers."
        ) from exc
    utility = getattr(prune_utils, attr, None)
    if utility is None:
        raise AttributeError(f"prune.utils missing expected helper: {attr}")
    return utility


def _require_extra(module: str, attr: str, caller: str):
    try:
        mod = importlib.import_module(module)
    except ImportError as exc:
        raise RuntimeError(
            f"{caller} requires optional dependency '{module}'. Install the extras package to enable this feature."
        ) from exc
    try:
        return getattr(mod, attr)
    except AttributeError as exc:
        raise RuntimeError(
            f"{caller} expected attribute '{attr}' in module '{module}'."
        ) from exc

def slice_lines(path: str, start: int, end: int) -> str:
    func = _require_prune_utils("slice_lines")
    return func(path, start, end)

def merge_selected_spans(
    spans: List[dict],
    root: str,
    out_file: str,
    start_label: str,
    end_label: str,
    *,
    fh: TextIO | None = None,
    append: bool = False,
    enumerate_lines: bool = False,
    remove_empty: bool = False,
) -> None:
    func = _require_prune_utils("merge_selected_spans")
    func(
        spans,
        root,
        out_file,
        start_label,
        end_label,
        fh=fh,
        append=append,
        enumerate_lines=enumerate_lines,
        remove_empty=remove_empty,
    )

@dataclass
class MergePlanResult:
    ok: bool
    files_with_summary_errors: List[str]
    errors_short: int
    errors_long: int


__all__ = [
    "FileProcessor",
    "merge_selected_spans",
    "slice_lines",
    "MergePlanResult",
]


class FileProcessor:

    def __init__(self) -> None:
        # Default directories to exclude
        self.default_excluded_dirs = {
            os.path.normpath(d)
            for d in {
                ".git",
                ".venv",
                "venv",
                "__pycache__",
                "addons",
                "fonts",
                "assets",
            }
        }
        self.excluded_dirs = set(self.default_excluded_dirs)

        # Default files to exclude
        self.default_excluded_files = {".DS_Store"}
        self.excluded_files = self.default_excluded_files.copy()

        # Cache for virtual environment checks
        self._venv_cache: dict[str, bool] = {}

        # ---- tokeniser setup -----------------
        self._encoding = core_tokens.get_encoding()
        self._enc_name = core_tokens.ENCODING_NAME
        self._enc_tag = core_tokens.get_encoding_tag()
        self._token_cache = core_token_cache.TokenCache(encoding_tag=self._enc_tag)

        # Custom extension overrides (ext → Category)
        self._custom_ext_map: dict[str, Category] = {}

    def set_custom_ext_map(self, mapping: dict[str, str]) -> None:
        out: dict[str, Category] = {}
        for k, v in (mapping or {}).items():
            ext = k.lower().lstrip(".")
            try:
                out[ext] = Category[v.upper()]
            except Exception:
                continue
        self._custom_ext_map = out

    def set_excluded_dirs(self, excluded_dirs: Set[str]) -> None:
        self.excluded_dirs = {
            norm for d in (excluded_dirs or set()) if (norm := _normalize_exclusion_entry(d))
        }

    def reset_excluded_dirs(self) -> None:
        self.excluded_dirs = set(self.default_excluded_dirs)

    def set_excluded_files(self, excluded_files: Set[str]) -> None:
        self.excluded_files = {
            norm for f in (excluded_files or set()) if (norm := _normalize_exclusion_entry(f))
        }

    def reset_excluded_files(self) -> None:
        self.excluded_files = self.default_excluded_files.copy()

    def is_virtual_env(self, dir_path: str) -> bool:
        norm = os.path.normpath(dir_path)
        cached = self._venv_cache.get(norm)
        if cached is not None:
            return cached

        indicators = [
            os.path.exists(os.path.join(norm, "pyvenv.cfg")),
            os.path.exists(os.path.join(norm, "bin", "activate")),
            os.path.exists(os.path.join(norm, "Scripts", "activate.bat")),
            os.path.exists(os.path.join(norm, "lib", "python")),
            os.path.exists(os.path.join(norm, "pip-selfcheck.json")),
        ]
        verdict = any(indicators)
        self._venv_cache[norm] = verdict
        return verdict

    @staticmethod
    def is_binary_file_static(file_path: str, chunk_size: int = 2048) -> bool:
        return core_io.is_binary_file(
            file_path,
            chunk_size=chunk_size,
        )

    def is_binary_file(self, file_path: str, chunk_size: int = 2048) -> bool:
        return core_io.is_binary_file(
            file_path,
            chunk_size=chunk_size,
            custom_ext_map=self._custom_ext_map,
        )

    def read_file_content(self, file_path: str) -> Optional[str]:
        return core_io.read_file_content(
            file_path,
            custom_ext_map=self._custom_ext_map,
        )

    def read_file_content_ex(self, file_path: str) -> tuple[Optional[str], str]:
        return core_io.read_file_content_ex(
            file_path,
            custom_ext_map=self._custom_ext_map,
        )

    def categorise(self, path: str, is_binary_hint: bool = False) -> Category:
        return core_classify.categorise(
            path,
            is_binary_hint=is_binary_hint,
            custom_ext_map=self._custom_ext_map,
            binary_check=self.is_binary_file,
        )

    # ───────────────────────── token cache helpers ──────────────────────────
    def _cache_get(self, path: str, size: int, mtime_ns: int) -> int | None:
        return self._token_cache.get(path, size, mtime_ns)

    def _cache_put(
        self, path: str, size: int, mtime_ns: int, tokens: int | None
    ) -> None:
        self._token_cache.put(path, size, mtime_ns, tokens)

    def _commit_pending_locked(self) -> None:
        self._token_cache.commit()

    def _commit_pending(self) -> None:
        self._token_cache.commit()

    def get_encoding_name(self) -> str:
        return self._enc_name

    def count_tokens(self, text: str) -> int:
        return core_tokens.count_tokens(text, encoding=self._encoding)

    def file_token_count(self, file_path: str) -> int | None:
        try:
            stat = os.stat(file_path)
        except (FileNotFoundError, PermissionError, OSError):
            return None
        try:
            mtime_ns = int(getattr(stat, "st_mtime_ns"))
        except Exception:
            mtime_ns = int(stat.st_mtime * 1_000_000_000)

        cached = self._cache_get(file_path, stat.st_size, mtime_ns)
        if cached is not None:
            if cached < 0:
                return None
            return cached

        content = self.read_file_content(file_path)
        if content is None:
            self._cache_put(file_path, stat.st_size, mtime_ns, None)
            return None
        normalized = estimate_mod.transform_content(content)
        tokens = self.count_tokens(normalized)
        self._cache_put(file_path, stat.st_size, mtime_ns, tokens)
        return tokens

    def processed_token_count(
        self,
        path: str,
        *,
        enumerate_lines: bool = False,
        remove_empty: bool = False,
    ) -> int:
        return estimate_mod.processed_token_count(
            self.read_file_content,
            self.count_tokens,
            path,
            enumerate_lines=enumerate_lines,
            remove_empty=remove_empty,
        )

    def transform_content(
        self,
        text: str,
        *,
        enumerate_lines: bool = False,
        remove_empty: bool = False,
    ) -> str:
        return estimate_mod.transform_content(
            text,
            enumerate_lines=enumerate_lines,
            remove_empty=remove_empty,
        )

    def structural_view(self, file_path: str, *, root: str | None = None):
        from codemerge.structure import extract_file_structure

        return extract_file_structure(
            file_path,
            root=root,
            token_counter=self.count_tokens,
        )

    def render_skeleton_block(
        self,
        file_path: str,
        *,
        root: str | None = None,
        enumerate_lines: bool = False,
        remove_empty: bool = False,
    ) -> str:
        from codemerge.structure import render_skeleton_block

        view = self.structural_view(file_path, root=root)
        text = render_skeleton_block(view)
        return self.transform_content(
            text,
            enumerate_lines=enumerate_lines,
            remove_empty=remove_empty,
        )

    def skeleton_token_count(
        self,
        file_path: str,
        *,
        root: str | None = None,
        enumerate_lines: bool = False,
        remove_empty: bool = False,
    ) -> int:
        return self.count_tokens(
            self.render_skeleton_block(
                file_path,
                root=root,
                enumerate_lines=enumerate_lines,
                remove_empty=remove_empty,
            )
        )

    def _merge_file_tokens(
        self,
        path: str,
        *,
        enumerate_lines: bool,
        remove_empty: bool,
    ) -> int:
        return estimate_mod.merge_file_tokens(
            self.file_token_count,
            lambda p, enumerate_lines=False, remove_empty=False: self.processed_token_count(
                p,
                enumerate_lines=enumerate_lines,
                remove_empty=remove_empty,
            ),
            path,
            enumerate_lines=enumerate_lines,
            remove_empty=remove_empty,
        )

    @staticmethod
    def _normalise_plan(
        plan: Dict[str, FileMode] | Dict[str, str],
        root_abs: str,
    ) -> tuple[Dict[str, FileMode], List[str]]:
        norm: Dict[str, FileMode] = {}
        order: List[str] = []
        for raw_path, raw_mode in plan.items():
            candidate = raw_path if os.path.isabs(raw_path) else os.path.join(root_abs, raw_path)
            abs_path = os.path.abspath(candidate)
            if abs_path not in norm:
                order.append(abs_path)
            if isinstance(raw_mode, FileMode):
                norm[abs_path] = raw_mode
                continue
            key = str(raw_mode or "").strip().upper()
            if key in FileMode.__members__:
                norm[abs_path] = FileMode[key]
                continue
            try:
                norm[abs_path] = FileMode(key)
            except Exception:
                norm[abs_path] = FileMode.CODE
        return norm, order

    @staticmethod
    def get_modes_from_config(cfg: Dict[str, object]) -> Dict[str, FileMode]:
        raw = cfg.get("per_file_modes") if isinstance(cfg, dict) else None
        if not isinstance(raw, dict):
            return {}

        result: Dict[str, FileMode] = {}
        for path, mode in raw.items():
            try:
                abs_path = os.path.abspath(path)
            except Exception:
                continue
            if isinstance(mode, FileMode):
                result[abs_path] = mode
                continue
            key = str(mode or "").strip().upper()
            if key in FileMode.__members__:
                result[abs_path] = FileMode[key]
                continue
            try:
                result[abs_path] = FileMode(key)
            except Exception:
                continue
        return result

    @staticmethod
    def save_modes_to_config(
        cfg: Dict[str, object],
        modes: Dict[str, FileMode | str],
    ) -> Dict[str, str]:
        serialised: Dict[str, str] = {}
        for path, mode in modes.items():
            try:
                abs_path = os.path.abspath(path)
            except Exception:
                continue
            if isinstance(mode, FileMode):
                serialised[abs_path] = mode.name
                continue
            key = str(mode or "").strip().upper()
            if key in FileMode.__members__:
                serialised[abs_path] = key
        if isinstance(cfg, dict):
            cfg["per_file_modes"] = serialised
        return serialised

    # --------------------------- High-level: plan merge ---------------------------
    def merge_with_plan(
        self,
        directory: str,
        plan: Dict[str, FileMode] | Dict[str, str],
        output_file: str,
        *,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        include_file_tree: bool = True,
        create_json_manifest: bool = False,
        custom_start_label: str = "======= START OF FILE: {file_display} =======",
        custom_end_label: str = "======= END OF FILE: {file_display} =======",
        file_tree_start_label: str = "======= FILE TREE =======",
        file_tree_end_label: str = "======= END OF FILE TREE =======",
        summaries_by_file: Optional[Dict[str, Optional[str]]] = None,
        summaries_short: Optional[Dict[str, Optional[str]]] = None,
        summaries_long: Optional[Dict[str, Optional[str]]] = None,
        strict_summary_required: bool | None = None,
        prompt_template: str | None = None,
        remove_empty: bool = False,
        enumerate_lines: bool = False,
        why_by_file: Optional[Dict[str, str]] = None,
        markdown_fences: bool = False,
        markdown_toc: bool = False,
        app_config: Optional[Dict] = None,
        output_stream: TextIO | None = None,
        virtual_blocks: Optional[List[Dict[str, str]]] = None,
        summarized_folder_rel_paths: Sequence[str] | None = None,
        pinned_file_rel_paths: Sequence[str] | None = None,
    ) -> MergePlanResult:
        """
        Merge based on a per-file plan mapping paths to FileMode decisions.

        Modes:
          * EXCLUDE → omit file entirely
          * CODE → include code only
          * SKELETON → include deterministic structural skeleton only
          * CODE_PLUS_SKELETON → include skeleton then code
          * CODE_PLUS_SHORT / CODE_PLUS_LONG → summary then code output
          * SUMMARY_SHORT / SUMMARY_LONG → summary-only output
          * SUMMARY_*_PLUS_SKELETON → summary then skeleton output

        Summaries are ensured via the orchestrator before merging. The method
        passes per-file overrides into the writer so renderer and estimator stay
        consistent.
        """

        ensure_summaries = _require_ensure_summaries()

        root_abs = os.path.abspath(directory)
        cfg = dict(app_config or AppConfig().get_config())
        cfg["auto_refresh_summaries"] = True
        cfg["auto_summarize_missing"] = True
        plan_size = len(plan) if hasattr(plan, "__len__") else 0
        metric_emit(
            M_EVENT["MERGE_PLAN_START"],
            root=root_abs,
            output=os.path.abspath(output_file),
            plan_size=plan_size,
            markdown_fences=bool(markdown_fences),
            markdown_toc=bool(markdown_toc),
        )

        norm_plan, order_abs = self._normalise_plan(plan, root_abs)

        exclude: Set[str] = set()
        code_files: List[str] = []
        sum_short: List[str] = []
        sum_long: List[str] = []
        code_plus_short: List[str] = []
        code_plus_long: List[str] = []
        files_to_merge: List[str] = []
        content_mode_map: Dict[str, str] = {}
        summaries_short_local: Dict[str, Optional[str]] = {}
        summaries_long_local: Dict[str, Optional[str]] = {}
        files_with_summary_errors: List[str] = []
        errors_short = 0
        errors_long = 0
        vendored_forced_code: List[str] = []

        for abs_path, mode in norm_plan.items():
            if mode is FileMode.EXCLUDE:
                exclude.add(abs_path)
                content_mode_map[abs_path] = "exclude"
            elif mode is FileMode.CODE:
                code_files.append(abs_path)
                content_mode_map[abs_path] = "code"
            elif mode in (FileMode.SKELETON, FileMode.API_SURFACE, FileMode.SIGNATURES):
                content_mode_map[abs_path] = "skeleton"
            elif mode is FileMode.CODE_PLUS_SKELETON:
                code_files.append(abs_path)
                content_mode_map[abs_path] = "code_plus_skeleton"
            elif mode is FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON:
                code_files.append(abs_path)
                code_plus_short.append(abs_path)
                sum_short.append(abs_path)
                content_mode_map[abs_path] = "code_plus_summary_short_plus_skeleton"
            elif mode is FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON:
                code_files.append(abs_path)
                code_plus_long.append(abs_path)
                sum_long.append(abs_path)
                content_mode_map[abs_path] = "code_plus_summary_long_plus_skeleton"
            elif mode is FileMode.CODE_PLUS_SHORT:
                code_files.append(abs_path)
                code_plus_short.append(abs_path)
                sum_short.append(abs_path)
                content_mode_map[abs_path] = "code_plus_short"
            elif mode is FileMode.CODE_PLUS_LONG:
                code_files.append(abs_path)
                code_plus_long.append(abs_path)
                sum_long.append(abs_path)
                content_mode_map[abs_path] = "code_plus_long"
            elif mode is FileMode.SUMMARY_SHORT:
                sum_short.append(abs_path)
                content_mode_map[abs_path] = "summary_short"
            elif mode is FileMode.SUMMARY_LONG:
                sum_long.append(abs_path)
                content_mode_map[abs_path] = "summary_long"
            elif mode is FileMode.SUMMARY_SHORT_PLUS_SKELETON:
                sum_short.append(abs_path)
                content_mode_map[abs_path] = "summary_short_plus_skeleton"
            elif mode is FileMode.SUMMARY_LONG_PLUS_SKELETON:
                sum_long.append(abs_path)
                content_mode_map[abs_path] = "summary_long_plus_skeleton"

        def _safe_rel(path: str) -> Optional[str]:
            try:
                return os.path.relpath(path, root_abs)
            except Exception:
                return None

        if not bool(cfg.get("summarize_vendored", False)) and (sum_short or sum_long):
            rels: List[str] = []
            for candidate in dict.fromkeys(sum_short + sum_long):
                rel = _safe_rel(candidate)
                if rel is not None:
                    rels.append(rel)
            vendored: Set[str] = set()
            if rels:
                vendored = self.git_vendored_set(root_abs, rels)

            if vendored:
                code_plus_paths = set(code_plus_short + code_plus_long)
                summary_plus_skeleton_modes = {
                    "summary_short_plus_skeleton",
                    "summary_long_plus_skeleton",
                }

                def _filter(paths: List[str], forced_code: List[str]) -> List[str]:
                    out: List[str] = []
                    for path in paths:
                        rel = _safe_rel(path)
                        if rel is not None and rel in vendored:
                            if path in code_plus_paths:
                                forced_code.append(path)
                            elif content_mode_map.get(path) in summary_plus_skeleton_modes:
                                content_mode_map[path] = "skeleton"
                            continue
                        out.append(path)
                    return out

                sum_short = _filter(sum_short, vendored_forced_code)
                sum_long = _filter(sum_long, vendored_forced_code)

                for path in dict.fromkeys(vendored_forced_code):
                    content_mode_map[os.path.abspath(path)] = "code"

        fallback_on_error = bool(cfg.get("fallback_to_code_on_summary_error", False))
        strict_required = (
            bool(cfg.get("strict_summary_required", False))
            if strict_summary_required is None
            else bool(strict_summary_required)
        )

        def _merge_summary_map(
            target: Dict[str, Optional[str]],
            source: Optional[Dict[str, Optional[str]]],
            *,
            override: bool,
        ) -> None:
            if not source:
                return
            for path, items in source.items():
                if not override and path in target:
                    continue
                target[path] = items
                target[os.path.abspath(path)] = items

        try:
            if sum_short:
                try:
                    fetched, errors = ensure_summaries(
                        sum_short,
                        "short",
                        self,
                        cfg,
                        progress_cb=None,
                        force=False,
                        root=root_abs,
                        strict_required=strict_required,
                    )
                except TypeError:
                    fetched, errors = ensure_summaries(
                        sum_short, "short", self, cfg, progress_cb=None
                    )
                for path, items in fetched.items():
                    abs_path = os.path.abspath(path)
                    summaries_short_local[path] = items
                    summaries_short_local[abs_path] = items
                files_with_summary_errors.extend(errors)
                errors_short = len(errors)

            if sum_long:
                try:
                    fetched, errors = ensure_summaries(
                        sum_long,
                        "long",
                        self,
                        cfg,
                        progress_cb=None,
                        force=False,
                        root=root_abs,
                        strict_required=strict_required,
                    )
                except TypeError:
                    fetched, errors = ensure_summaries(
                        sum_long, "long", self, cfg, progress_cb=None
                    )
                for path, items in fetched.items():
                    abs_path = os.path.abspath(path)
                    summaries_long_local[path] = items
                    summaries_long_local[abs_path] = items
                files_with_summary_errors.extend(errors)
                errors_long = len(errors)

            _merge_summary_map(summaries_short_local, summaries_by_file, override=False)
            _merge_summary_map(summaries_long_local, summaries_by_file, override=False)
            _merge_summary_map(summaries_short_local, summaries_short, override=True)
            _merge_summary_map(summaries_long_local, summaries_long, override=True)

            seen_errors: Set[str] = set()
            dedup_errors: List[str] = []
            for err in files_with_summary_errors:
                abs_err = os.path.abspath(err)
                if abs_err in seen_errors:
                    continue
                seen_errors.add(abs_err)
                dedup_errors.append(abs_err)
            files_with_summary_errors = dedup_errors

            if strict_required:
                missing: List[str] = []
                for path in sum_short:
                    abs_path = os.path.abspath(path)
                    if (
                        path not in summaries_short_local
                        and abs_path not in summaries_short_local
                    ):
                        missing.append(path)
                        continue
                    if summaries_short_local.get(path) is None and summaries_short_local.get(abs_path) is None:
                        missing.append(path)
                for path in sum_long:
                    abs_path = os.path.abspath(path)
                    if (
                        path not in summaries_long_local
                        and abs_path not in summaries_long_local
                    ):
                        missing.append(path)
                        continue
                    if summaries_long_local.get(path) is None and summaries_long_local.get(abs_path) is None:
                        missing.append(path)
                if files_with_summary_errors or missing:
                    detail = []
                    if files_with_summary_errors:
                        detail.append(f"errors: {files_with_summary_errors}")
                    if missing:
                        detail.append(f"missing: {missing}")
                    raise RuntimeError(
                        "Summary required but unavailable (" + "; ".join(detail) + ")"
                    )

            seen_order: Set[str] = set()
            for abs_path in order_abs:
                if abs_path in exclude or abs_path in seen_order:
                    continue
                seen_order.add(abs_path)
                files_to_merge.append(abs_path)

            total_files = len(files_to_merge)

            def _percent_progress_adapter(
                cb: Optional[Callable[[int, int], None]], total: int
            ) -> Optional[Callable[[float, str], None]]:
                if cb is None:
                    return None
                if total <= 0:
                    def _complete(_pct: float, _msg: str) -> None:
                        cb(0, 0)

                    return _complete

                def _inner(progress_pct: float, _msg: str) -> None:
                    done = int(round((progress_pct / 100.0) * total))
                    done = max(0, min(done, total))
                    cb(done, total)

                return _inner

            progress_wrapped = _percent_progress_adapter(progress_callback, total_files)

            ok = merge_basic_mod.merge_files(
                self,
                directory=directory,
                files_to_merge=files_to_merge,
                output_file=output_file,
                progress_callback=progress_wrapped,
                include_file_tree=include_file_tree,
                include_summaries=False,
                custom_start_label=custom_start_label,
                custom_end_label=custom_end_label,
                file_tree_start_label=file_tree_start_label,
                file_tree_end_label=file_tree_end_label,
                create_json_manifest=create_json_manifest,
                summaries_by_file=None,
                summaries_short=summaries_short_local,
                summaries_long=summaries_long_local,
                content_mode="code_only",
                files_with_summary_errors=files_with_summary_errors,
                prompt_template=prompt_template,
                output_stream=output_stream,
                remove_empty=remove_empty,
                enumerate_lines=enumerate_lines,
                why_by_file=why_by_file,
                markdown_fences=markdown_fences,
                markdown_toc=markdown_toc,
                format_summary=_format_summary,
                content_mode_map=content_mode_map,
                fallback_to_code_on_summary_error=fallback_on_error,
                strict_summary_required=strict_required,
                virtual_blocks=virtual_blocks,
                summarized_folder_rel_paths=summarized_folder_rel_paths,
                pinned_file_rel_paths=pinned_file_rel_paths,
            )
            return MergePlanResult(
                ok=ok,
                files_with_summary_errors=files_with_summary_errors,
                errors_short=errors_short,
                errors_long=errors_long,
            )
        finally:
            code_count = sum(1 for mode in content_mode_map.values() if mode == "code")
            code_plus_short_count = sum(
                1 for mode in content_mode_map.values() if mode == "code_plus_short"
            )
            code_plus_long_count = sum(
                1 for mode in content_mode_map.values() if mode == "code_plus_long"
            )
            skeleton_count = sum(
                1
                for mode in content_mode_map.values()
                if "skeleton" in str(mode)
            )
            summary_short_count = sum(
                1 for mode in content_mode_map.values() if mode == "summary_short"
            )
            summary_long_count = sum(
                1 for mode in content_mode_map.values() if mode == "summary_long"
            )
            metric_emit(
                M_EVENT["MERGE_PLAN_END"],
                root=root_abs,
                output=os.path.abspath(output_file),
                files=len(files_to_merge),
                excluded=len(exclude),
                code=code_count,
                summary_short=summary_short_count,
                summary_long=summary_long_count,
                code_plus_short=code_plus_short_count,
                code_plus_long=code_plus_long_count,
                skeleton=skeleton_count,
                summary_errors=len(files_with_summary_errors),
                summary_errors_short=errors_short,
                summary_errors_long=errors_long,
            )

    # ------------------------------- Watch helpers -------------------------------
    def watch_tick(
        self,
        directory: str,
        plan: Dict[str, FileMode] | Dict[str, str],
        *,
        app_config: Optional[Dict] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, object]:
        """Execute a single watch cycle that ensures required summaries."""

        try:
            from codemerge.summarizer.shared.watch import tick as _tick
        except Exception as exc:  # pragma: no cover - optional module during rollout
            raise RuntimeError("summarizer.shared.watch.tick is unavailable") from exc

        cfg = dict(app_config or AppConfig().get_config())
        norm_plan, _ = self._normalise_plan(plan, os.path.abspath(directory))
        return _tick(directory, norm_plan, self, cfg, progress_cb=progress_callback)

    def watch_loop(
        self,
        directory: str,
        plan: Dict[str, FileMode] | Dict[str, str],
        *,
        app_config: Optional[Dict] = None,
        interval_sec: Optional[int] = None,
        stop_flag: Optional[Callable[[], bool]] = None,
        on_tick: Optional[Callable[[Dict[str, object]], None]] = None,
    ) -> None:
        """Continuously call :meth:`watch_tick` until ``stop_flag`` ends the loop."""

        try:
            from codemerge.summarizer.shared.watch import loop as _loop
        except Exception as exc:  # pragma: no cover - optional module during rollout
            raise RuntimeError("summarizer.shared.watch.loop is unavailable") from exc

        cfg = dict(app_config or AppConfig().get_config())
        _loop(
            directory,
            self._normalise_plan(plan, os.path.abspath(directory))[0],
            self,
            cfg,
            interval_sec=interval_sec,
            stop_flag=stop_flag,
            on_tick=on_tick,
        )

    def gather_files(
        self,
        directory: str,
        file_types: Dict[str, bool],
        include_all: bool,
        sort_by: str = "name",
        sort_descending: bool = False,
        ignore_binary: bool = False,
        max_file_size_mb: float = 0.0,
        max_depth: int = 0,
        follow_symlinks: bool = False,
    ) -> List[str]:
        return scan_mod.gather_files(
            self,
            directory,
            file_types,
            include_all,
            sort_by=sort_by,
            sort_descending=sort_descending,
            ignore_binary=ignore_binary,
            max_file_size_mb=max_file_size_mb,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
        )

    # ------------------------------ GIT helpers ------------------------------
    def git_files(self, root: str, include_untracked: bool = False) -> list[str]:
        return git_mod.git_files(root, include_untracked=include_untracked)

    def git_vendored_set(self, root: str, rel_paths: list[str]) -> set[str]:
        return git_mod.git_vendored_set(root, rel_paths)

    # ------------------------ Xcode / pbxproj helpers ------------------------
    def xcode_target_sources(self, root: str) -> dict[str, set[str]]:
        func = _require_extra(
            "codemerge.filemerger.extras.xcode",
            "xcode_target_sources",
            "xcode_target_sources",
        )
        return func(root)

    # ------------------------ Context pack helpers ------------------------
    def build_pack(
        self,
        repo_path: str,
        subsystem_name: str,
        mode: str,
        output_path: str | None = None,
        *,
        return_text: bool | None = None,
        app_config: Optional[Dict] = None,
    ):
        from .context_pack import ContextPackFilters, build_pack as _build_pack

        cfg = dict(app_config or AppConfig().get_config())
        excluded_dirs = [
            str(x) for x in (cfg.get("excluded_dirs") or []) if isinstance(x, str) and x.strip()
        ]
        excluded_files = [
            str(x) for x in (cfg.get("excluded_files") or []) if isinstance(x, str) and x.strip()
        ]
        custom_excluded = [
            str(x) for x in (cfg.get("custom_excluded") or []) if isinstance(x, str) and x.strip()
        ]
        try:
            from .services import scan_service

            noise_dirs = scan_service.compute_noise_exclusions(set(), cfg)
            for entry in noise_dirs:
                if entry:
                    excluded_dirs.append(str(entry))
        except Exception:
            pass

        filters = ContextPackFilters(
            excluded_dirs=tuple(sorted(set(excluded_dirs))),
            excluded_files=tuple(sorted(set(excluded_files))),
            custom_excluded=tuple(sorted(set(custom_excluded))),
        )
        return _build_pack(
            repo_path,
            subsystem_name,
            mode,
            output_path,
            return_text=return_text,
            filters=filters,
            is_virtual_env=self.is_virtual_env,
        )

    def build_subsystem_context_pack(
        self,
        repo_path: str,
        subsystem_name: str,
        output_path: str | None = None,
        *,
        return_text: bool | None = None,
        app_config: Optional[Dict] = None,
    ):
        return self.build_pack(
            repo_path,
            subsystem_name,
            "subsystem",
            output_path,
            return_text=return_text,
            app_config=app_config,
        )

    def scan_directory(
        self,
        directory: str,
        file_types: Dict[str, bool],
        include_all: bool,
        sort_by: str = "name",
        sort_descending: bool = False,
        ignore_binary: bool = False,
        max_file_size_mb: float = 0.0,
        token_workers: int = 0,
        max_depth: int = 0,
        follow_symlinks: bool = False,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> ScanContext:
        ctx = scan_mod.scan_directory(
            self,
            directory,
            file_types,
            include_all,
            sort_by=sort_by,
            sort_descending=sort_descending,
            ignore_binary=ignore_binary,
            max_file_size_mb=max_file_size_mb,
            token_workers=token_workers,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
            progress_callback=progress_callback,
        )
        self._commit_pending()
        return ctx

    def scan_file_list(
        self,
        root: str,
        files: List[str],
        sort_by: str = "name",
        sort_descending: bool = False,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> ScanContext:
        ctx = scan_mod.scan_file_list(
            self,
            root,
            files,
            sort_by=sort_by,
            sort_descending=sort_descending,
            progress_callback=progress_callback,
        )
        self._commit_pending()
        return ctx

    def generate_file_tree(
        self,
        directory: str,
        files: List[str],
        ascii_mode: bool | None = None,
        summarized_folders: Sequence[str] | None = None,
        pinned_files: Sequence[str] | None = None,
    ) -> str:
        return scan_mod.generate_file_tree(
            directory,
            files,
            ascii_mode=ascii_mode,
            summarized_folders=summarized_folders,
            pinned_files=pinned_files,
        )

    def merge_files(
        self,
        directory: str,
        files_to_merge: List[str],
        output_file: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        include_file_tree: bool = True,
        include_summaries: bool = False,  # legacy param (not used directly now)
        custom_start_label: str = "======= START OF FILE: {file_display} =======",
        custom_end_label: str = "======= END OF FILE: {file_display} =======",
        file_tree_start_label: str = "======= FILE TREE =======",
        file_tree_end_label: str = "======= END OF FILE TREE =======",
        create_json_manifest: bool = False,
        summaries_by_file: Optional[Dict[str, Optional[str]]] = None,
        content_mode: str = "code_only",
        files_with_summary_errors: Optional[List[str]] = None,
        prompt_template: str | None = None,
        output_stream: TextIO | None = None,
        remove_empty: bool = False,
        enumerate_lines: bool = False,
        why_by_file: Optional[Dict[str, str]] = None,
        markdown_fences: bool = False,
        markdown_toc: bool = False,
        summaries_short: Optional[Dict[str, Optional[str]]] = None,
        summaries_long: Optional[Dict[str, Optional[str]]] = None,
        strict_summary_required: bool = False,
        content_mode_map: Optional[Dict[str, str]] = None,
        fallback_to_code_on_summary_error: bool = True,
        virtual_blocks: Optional[List[Dict[str, str]]] = None,
        summarized_folder_rel_paths: Optional[Sequence[str]] = None,
        pinned_file_rel_paths: Optional[Sequence[str]] = None,
    ) -> bool:
        try:
            return merge_basic_mod.merge_files(
                self,
                directory=directory,
                files_to_merge=files_to_merge,
                output_file=output_file,
                progress_callback=progress_callback,
                include_file_tree=include_file_tree,
                include_summaries=include_summaries,
                custom_start_label=custom_start_label,
                custom_end_label=custom_end_label,
                file_tree_start_label=file_tree_start_label,
                file_tree_end_label=file_tree_end_label,
                create_json_manifest=create_json_manifest,
                summaries_by_file=summaries_by_file,
                summaries_short=summaries_short,
                summaries_long=summaries_long,
                strict_summary_required=strict_summary_required,
                content_mode=content_mode,
                files_with_summary_errors=files_with_summary_errors,
                content_mode_map=content_mode_map,
                fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
                prompt_template=prompt_template,
                output_stream=output_stream,
                remove_empty=remove_empty,
                enumerate_lines=enumerate_lines,
                why_by_file=why_by_file,
                markdown_fences=markdown_fences,
                markdown_toc=markdown_toc,
                format_summary=_format_summary,
                virtual_blocks=virtual_blocks,
                summarized_folder_rel_paths=summarized_folder_rel_paths,
                pinned_file_rel_paths=pinned_file_rel_paths,
            )
        except Exception:
            logger.exception("Error merging files into %s", output_file)
            raise

    def check_file_exists(self, filepath: str) -> bool:
        """Check if a file already exists."""
        return os.path.exists(filepath)

    def generate_alternative_filename(self, filepath: str) -> str:
        """
        Generate an alternative filename by appending a numeric counter
        if the given filepath already exists.
        """
        base, ext = os.path.splitext(filepath)
        counter = 2
        new_path = f"{base}_{counter}{ext}"

        while os.path.exists(new_path):
            counter += 1
            new_path = f"{base}_{counter}{ext}"

        return new_path

    def merge_selected_spans(
        self,
        spans: List[dict],
        root: str,
        out_file: str,
        start_label: str,
        end_label: str,
        *,
        fh: TextIO | None = None,
        append: bool = False,
        enumerate_lines: bool = False,
        remove_empty: bool = False,
    ) -> None:
        """Public wrapper around :func:`merge_selected_spans`."""

        merge_selected_spans(
            spans,
            root,
            out_file,
            start_label,
            end_label,
            fh=fh,
            append=append,
            enumerate_lines=enumerate_lines,
            remove_empty=remove_empty,
        )

    # >>> NEW/UPDATED SECTION: multi-file merging logic (our complicated function) <<<

    def merge_files_into_multiple(
        self,
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
        summarized_folder_rel_paths: Optional[Sequence[str]] = None,
        pinned_file_rel_paths: Optional[Sequence[str]] = None,
        markdown_fences: bool = False,
        markdown_toc: bool = False,
    ) -> None:
        merge_fn = _require_extra(
            "codemerge.filemerger.extras.split_lines",
            "merge_files_into_multiple",
            "merge_files_into_multiple",
        )

        merge_fn(
            self,
            directory,
            files_to_merge,
            output_folder,
            num_text_files,
            progress_callback,
            custom_start_label,
            custom_end_label,
            file_tree_start_label,
            file_tree_end_label,
            include_file_tree,
            create_json_manifest,
            summaries_by_file,
            content_mode,
            files_with_summary_errors=files_with_summary_errors,
            prompt_template=prompt_template,
            verb=verb,
            remove_empty=remove_empty,
            enumerate_lines=enumerate_lines,
            content_mode_map=content_mode_map,
            fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            strict_summary_required=strict_summary_required,
            virtual_blocks=virtual_blocks,
            summarized_folder_rel_paths=summarized_folder_rel_paths,
            pinned_file_rel_paths=pinned_file_rel_paths,
            markdown_fences=markdown_fences,
            markdown_toc=markdown_toc,
        )

    def __del__(self) -> None:
        try:
            self._token_cache.close()
        except Exception:
            pass

    def _extract_merged_files(
        self,
        merged_path: str,
        custom_start_label: str = "======= START OF FILE: {file_display} =======",
        custom_end_label: str = "======= END OF FILE: {file_display} =======",
    ) -> Dict[str, str]:
        extract_fn = _require_extra(
            "codemerge.filemerger.extras.restore",
            "extract_merged_files",
            "_extract_merged_files",
        )

        return extract_fn(
            merged_path,
            custom_start_label=custom_start_label,
            custom_end_label=custom_end_label,
        )

    def restore_from_merged(
        self,
        merged_path: str,
        output_dir: str,
        custom_start_label: str = "======= START OF FILE: {file_display} =======",
        custom_end_label: str = "======= END OF FILE: {file_display} =======",
    ) -> None:
        restore_fn = _require_extra(
            "codemerge.filemerger.extras.restore",
            "restore_from_merged",
            "restore_from_merged",
        )

        restore_fn(
            self,
            merged_path,
            output_dir,
            custom_start_label=custom_start_label,
            custom_end_label=custom_end_label,
        )

    def _collect_source_files(
        self,
        source: str,
    ) -> Dict[str, str]:
        collect_fn = _require_extra(
            "codemerge.filemerger.extras.restore",
            "collect_source_files",
            "_collect_source_files",
        )

        return collect_fn(self, source)

    def pair_codebases(
        self,
        src_a: str,
        src_b: str,
        dest_dir: str,
        keep_structure: bool = True,
        label_a: Optional[str] = None,
        label_b: Optional[str] = None,
    ) -> None:
        pair_fn = _require_extra(
            "codemerge.filemerger.extras.pairing",
            "pair_codebases",
            "pair_codebases",
        )

        pair_fn(
            self,
            src_a,
            src_b,
            dest_dir,
            keep_structure=keep_structure,
            label_a=label_a,
            label_b=label_b,
        )

    def pair_selected_files(
        self,
        directory: str,
        selected_files: List[str],
        other_source: str,
        dest_dir: str,
        keep_structure: bool = True,
        label_a: Optional[str] = None,
        label_b: Optional[str] = None,
    ) -> None:
        pair_fn = _require_extra(
            "codemerge.filemerger.extras.pairing",
            "pair_selected_files",
            "pair_selected_files",
        )

        pair_fn(
            self,
            directory,
            selected_files,
            other_source,
            dest_dir,
            keep_structure=keep_structure,
            label_a=label_a,
            label_b=label_b,
        )

    def compare_codebases_ai(
        self,
        src_a: str,
        src_b: str,
    ) -> Dict[str, str]:
        compare_fn = _require_extra(
            "codemerge.filemerger.extras.compare_ai",
            "compare_codebases_ai",
            "compare_codebases_ai",
        )

        return compare_fn(self, src_a, src_b)

    def format_comparison_report(
        self,
        results: Dict[str, str],
        *,
        src_a: str,
        src_b: str,
    ) -> str:
        format_fn = _require_extra(
            "codemerge.filemerger.extras.compare_ai",
            "format_comparison_report",
            "format_comparison_report",
        )

        return format_fn(results, src_a=src_a, src_b=src_b)

    def merge_group_token_details(
        self,
        directory: str,
        files: list[str],
        *,
        include_file_tree: bool,
        custom_start_label: str,
        custom_end_label: str,
        file_tree_start_label: str,
        file_tree_end_label: str,
        prompt_template: str | None = None,
        remove_empty: bool = False,
        enumerate_lines: bool = False,
        markdown_fences: bool = False,
        markdown_toc: bool = False,
        content_mode: str = "code_only",
        summaries_by_file: Optional[Dict[str, Optional[str]]] = None,
        summaries_short: Optional[Dict[str, Optional[str]]] = None,
        summaries_long: Optional[Dict[str, Optional[str]]] = None,
        content_mode_map: Optional[Dict[str, str]] = None,
        files_with_summary_errors: Optional[List[str]] = None,
        fallback_to_code_on_summary_error: bool = True,
        why_by_file: Optional[Dict[str, str]] = None,
        strict_summary_required: bool = False,
        virtual_blocks: Optional[List[Dict[str, str]]] = None,
        summarized_folder_rel_paths: Sequence[str] | None = None,
        pinned_file_rel_paths: Sequence[str] | None = None,
    ) -> tuple[int, int]:
        return estimate_mod.merge_group_token_details(
            directory,
            files,
            include_file_tree=include_file_tree,
            custom_start_label=custom_start_label,
            custom_end_label=custom_end_label,
            file_tree_start_label=file_tree_start_label,
            file_tree_end_label=file_tree_end_label,
            prompt_template=prompt_template,
            remove_empty=remove_empty,
            enumerate_lines=enumerate_lines,
            markdown_fences=markdown_fences,
            markdown_toc=markdown_toc,
            content_mode=content_mode,
            summaries_by_file=summaries_by_file,
            summaries_short=summaries_short,
            summaries_long=summaries_long,
            read_file_content_ex=self.read_file_content_ex,
            count_tokens=self.count_tokens,
            generate_file_tree_fn=self.generate_file_tree,
            merge_file_tokens_fn=self._merge_file_tokens,
            render_skeleton_block_fn=lambda p, enumerate_lines=False, remove_empty=False: self.render_skeleton_block(
                p,
                root=directory,
                enumerate_lines=enumerate_lines,
                remove_empty=remove_empty,
            ),
            format_summary=_format_summary,
            content_mode_map=content_mode_map,
            files_with_summary_errors=files_with_summary_errors,
            fallback_to_code_on_summary_error=fallback_to_code_on_summary_error,
            why_by_file=why_by_file,
            strict_summary_required=strict_summary_required,
            virtual_blocks=virtual_blocks,
            summarized_folder_rel_paths=summarized_folder_rel_paths,
            pinned_file_rel_paths=pinned_file_rel_paths,
        )

    # >>> NEW: split so that each group stays ≤ token_limit
    def group_files_by_tokens(
        self,
        files: List[str],
        token_limit: int,
    ) -> List[List[str]]:
        group_fn = _require_extra(
            "codemerge.filemerger.extras.token_split",
            "group_files_by_tokens",
            "group_files_by_tokens",
        )

        return group_fn(self, files, token_limit)

    def merge_files_token_split(
        self,
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
        summarized_folder_rel_paths: Optional[Sequence[str]] = None,
        pinned_file_rel_paths: Optional[Sequence[str]] = None,
    ) -> None:
        merge_fn = _require_extra(
            "codemerge.filemerger.extras.token_split",
            "merge_files_token_split",
            "merge_files_token_split",
        )

        merge_fn(
            self,
            directory,
            files_to_merge,
            output_folder,
            token_limit,
            progress_callback,
            custom_start_label,
            custom_end_label,
            file_tree_start_label,
            file_tree_end_label,
            include_file_tree,
            create_json_manifest,
            markdown_fences,
            markdown_toc,
            summaries_by_file,
            content_mode,
            files_with_summary_errors=files_with_summary_errors,
            prompt_template=prompt_template,
            verb=verb,
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
