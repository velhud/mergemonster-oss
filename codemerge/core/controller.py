from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..filemerger import FileProcessor
from ..filemerger.scan import append_files_to_context, find_always_include_files
from ..filemerger.exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)
from ..filemerger.services import git_service, scan_service
from ..filemerger import git as git_mod
from ..filemerger.services.dto import ProgressCb, UiState
from ..models import FileMeta, FileMode, ScanContext


@dataclass(slots=True)
class FilterResult:
    visible_files: List[FileMeta]
    folder_totals: Dict[str, Tuple[int, int]]
    duplicate_set: Set[str]
    warnings: List[str]
    pattern_hits: Set[str]
    content_mode_map: Dict[str, str]


class CoreController:
    """Headless coordinator for scan/filter/mode logic shared by all UIs."""

    def __init__(
        self,
        *,
        file_processor: FileProcessor | None = None,
    ) -> None:
        self.fp = file_processor or FileProcessor()

        self.scan_ctx: ScanContext | None = None
        self.visible_files: list[FileMeta] = []
        self.folder_totals: dict[str, tuple[int, int]] = {}
        self.dup_set: set[str] = set()
        self.pattern_hits: set[str] = set()

        self.custom_excluded: set[str] = set()
        self.manual_includes: set[str] = set()
        self.per_file_modes: dict[str, FileMode] = {}
        self.per_folder_modes: dict[str, str] = {}

        self._default_mode: FileMode = FileMode.CODE
        self._mode_cycle_order: tuple[FileMode, ...] = (
            FileMode.CODE,
            FileMode.SKELETON,
            FileMode.API_SURFACE,
            FileMode.SIGNATURES,
            FileMode.CODE_PLUS_SKELETON,
            FileMode.SUMMARY_SHORT,
            FileMode.SUMMARY_LONG,
            FileMode.SUMMARY_SHORT_PLUS_SKELETON,
            FileMode.SUMMARY_LONG_PLUS_SKELETON,
            FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
            FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
            FileMode.CODE_PLUS_SHORT,
            FileMode.CODE_PLUS_LONG,
            FileMode.EXCLUDE,
        )

        self._git_changed_cache: dict[tuple[str, str, str], set[str]] = {}
        self._token_cache: dict[tuple[str, bool, bool], int] = {}
        self._duplicate_hash_cache: dict[tuple[str, int, int, int], str] = {}
        self._xcode_sources_root: Path | None = None
        self._xcode_target_sources: dict[str, set[str]] = {}

        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Configuration sync
    # ------------------------------------------------------------------
    def configure(self, cfg: Dict[str, Any] | None) -> None:
        """Sync controller state from a config snapshot."""
        cfg = cfg if isinstance(cfg, dict) else {}
        with self._lock:
            if "custom_ext_map" in cfg:
                try:
                    self.fp.set_custom_ext_map(cfg.get("custom_ext_map", {}) or {})
                except Exception:
                    pass

            if "excluded_dirs" in cfg:
                dirs = cfg.get("excluded_dirs", [])
                if isinstance(dirs, (list, set, tuple)):
                    try:
                        self.fp.set_excluded_dirs(set(dirs))
                    except Exception:
                        pass

            if "excluded_files" in cfg:
                files = cfg.get("excluded_files", [])
                if isinstance(files, (list, set, tuple)):
                    try:
                        self.fp.set_excluded_files(set(files))
                    except Exception:
                        pass

            if "custom_excluded" in cfg:
                raw = cfg.get("custom_excluded", [])
                if isinstance(raw, (list, set, tuple)):
                    self.custom_excluded = {str(x) for x in raw if isinstance(x, str) and x}
                else:
                    self.custom_excluded = set()

            if "per_file_modes" in cfg and hasattr(self.fp, "get_modes_from_config"):
                try:
                    self.per_file_modes = self.fp.get_modes_from_config(cfg)
                except Exception:
                    self.per_file_modes = {}

            if "per_folder_modes" in cfg:
                raw = cfg.get("per_folder_modes", {})
                if isinstance(raw, dict):
                    cleaned: dict[str, str] = {}
                    for k, v in raw.items():
                        if not isinstance(k, str) or not k:
                            continue
                        mode = str(v or "").strip().upper()
                        if mode == "FOLDER_SUMMARY":
                            mode = "FOLDER_SUMMARY_QUICK"
                        if mode in {"FOLDER_SUMMARY_QUICK", "FOLDER_SUMMARY_FULL"}:
                            cleaned[self._normalise_abs(k)] = mode
                    self.per_folder_modes = cleaned
                else:
                    self.per_folder_modes = {}

            self._default_mode = self._resolve_default_mode(cfg.get("summary_default_mode", "CODE"))

    @staticmethod
    def _resolve_default_mode(raw: object) -> FileMode:
        try:
            if isinstance(raw, FileMode):
                return raw
            key = str(raw or "CODE").strip().upper()
            if key in FileMode.__members__:
                return FileMode[key]
            return FileMode(key)
        except Exception:
            return FileMode.CODE

    # ------------------------------------------------------------------
    # Per-file mode helpers
    # ------------------------------------------------------------------
    @staticmethod
    def coerce_file_mode(value: object) -> FileMode | None:
        if value is None:
            return None
        if isinstance(value, FileMode):
            return value
        s = str(value).strip()
        if not s:
            return None
        up = s.upper()
        if up in FileMode.__members__:
            return FileMode[up]
        low = s.lower()
        # Accept UI content_mode values.
        mapping = {
            "code_only": FileMode.CODE,
            "skeleton_only": FileMode.SKELETON,
            "api_surface": FileMode.API_SURFACE,
            "signatures": FileMode.SIGNATURES,
            "code_plus_skeleton": FileMode.CODE_PLUS_SKELETON,
            "code_plus_short_plus_skeleton": FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
            "code_plus_long_plus_skeleton": FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
            "code_plus_short": FileMode.CODE_PLUS_SHORT,
            "code_plus_long": FileMode.CODE_PLUS_LONG,
            "short_only": FileMode.SUMMARY_SHORT,
            "long_only": FileMode.SUMMARY_LONG,
            "short_plus_skeleton": FileMode.SUMMARY_SHORT_PLUS_SKELETON,
            "long_plus_skeleton": FileMode.SUMMARY_LONG_PLUS_SKELETON,
            "exclude": FileMode.EXCLUDE,
        }
        return mapping.get(low)

    def default_file_mode(self, *, override: object | None = None) -> FileMode:
        mode = self.coerce_file_mode(override)
        if mode is not None:
            return mode
        return self._default_mode

    def get_mode(self, path: str) -> FileMode:
        abs_path = self._normalise_abs(path)
        with self._lock:
            explicit = self.per_file_modes.get(abs_path)
            if explicit is not None:
                return explicit
            for pref in self._summary_prefixes(self.per_folder_modes):
                if abs_path.startswith(pref):
                    return FileMode.EXCLUDE
            return self._default_mode

    def get_folder_mode(self, folder_path: str) -> str | None:
        abs_path = self._normalise_abs(folder_path)
        with self._lock:
            return self.per_folder_modes.get(abs_path)

    def set_mode(self, paths: Sequence[str], mode: FileMode | str | None) -> bool:
        resolved = self.coerce_file_mode(mode) if mode is not None else None
        changed = False
        with self._lock:
            for path in paths:
                abs_path = self._normalise_abs(path)
                if not abs_path:
                    continue
                if resolved is None:
                    if abs_path in self.per_file_modes:
                        self.per_file_modes.pop(abs_path, None)
                        changed = True
                    continue
                if self.per_file_modes.get(abs_path) is not resolved:
                    self.per_file_modes[abs_path] = resolved
                    changed = True
        return changed

    def set_folder_mode(self, folders: Sequence[str], mode: str | None) -> bool:
        m = (str(mode or "").strip().upper() if mode else "")
        if m == "FOLDER_SUMMARY":
            m = "FOLDER_SUMMARY_QUICK"
        if m and m not in {"FOLDER_SUMMARY_QUICK", "FOLDER_SUMMARY_FULL"}:
            raise ValueError("invalid folder mode")
        changed = False
        with self._lock:
            for folder in folders:
                abs_folder = self._normalise_abs(folder)
                if not abs_folder:
                    continue
                if not m:
                    if abs_folder in self.per_folder_modes:
                        self.per_folder_modes.pop(abs_folder, None)
                        changed = True
                else:
                    if self.per_folder_modes.get(abs_folder) != m:
                        self.per_folder_modes[abs_folder] = m
                        changed = True
        return changed

    def folder_modes_snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self.per_folder_modes)

    def cycle_mode(self, paths: Sequence[str]) -> FileMode:
        if not paths:
            return self._default_mode
        first = self.get_mode(paths[0])
        try:
            idx = self._mode_cycle_order.index(first)
            next_mode = self._mode_cycle_order[(idx + 1) % len(self._mode_cycle_order)]
        except ValueError:
            next_mode = self._default_mode
        self.set_mode(paths, next_mode)
        return next_mode

    def build_plan(
        self,
        files: Iterable[str],
        *,
        default_override: object | None = None,
    ) -> Dict[str, FileMode]:
        default_mode = self.default_file_mode(override=default_override)
        plan: Dict[str, FileMode] = {}
        with self._lock:
            folder_prefixes = self._summary_prefixes(self.per_folder_modes)
            for raw_path in files:
                abs_path = self._normalise_abs(raw_path)
                if not abs_path:
                    continue
                explicit = self.per_file_modes.get(abs_path)
                if explicit is not None:
                    plan[abs_path] = explicit
                    continue
                covered = any(abs_path.startswith(pref) for pref in folder_prefixes)
                if covered:
                    plan[abs_path] = FileMode.EXCLUDE
                else:
                    plan[abs_path] = default_mode
        return plan

    def content_mode_map_for(self, files: Iterable[str]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        default_mode = self.default_file_mode()
        with self._lock:
            folder_prefixes = self._summary_prefixes(self.per_folder_modes)
            for raw_path in files:
                abs_path = self._normalise_abs(raw_path)
                if not abs_path:
                    continue
                explicit = self.per_file_modes.get(abs_path)
                if explicit is not None:
                    mode = explicit
                else:
                    covered = any(abs_path.startswith(pref) for pref in folder_prefixes)
                    mode = FileMode.EXCLUDE if covered else default_mode
                mapping[abs_path] = self._mode_to_content_str(mode)
        return mapping

    @staticmethod
    def _summary_prefixes(folder_modes: Dict[str, str]) -> list[str]:
        prefixes = [
            p.rstrip(os.sep) + os.sep
            for p, m in folder_modes.items()
            if str(m or "").upper().startswith("FOLDER_SUMMARY")
        ]
        prefixes.sort(key=len, reverse=True)
        return prefixes

    @staticmethod
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

    @staticmethod
    def _normalise_abs(path: str) -> str:
        if not path:
            return ""
        try:
            return os.path.abspath(path)
        except Exception:
            return path

    # ------------------------------------------------------------------
    # Scan & filter
    # ------------------------------------------------------------------
    def scan(
        self,
        ui: UiState,
        *,
        progress_cb: ProgressCb | None = None,
        token_workers: int | None = None,
        custom_excluded: Set[str] | None = None,
        ignore_excludes: bool = False,
    ) -> ScanContext:
        """Scan according to UiState and store scan_ctx."""
        root_path = str(ui.root_dir)
        if not root_path:
            raise ValueError("root_dir is required")

        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            raise ValueError(f"root_dir is not a directory: {root_path}")

        with self._lock:
            root_resolved = self._safe_resolve(root)
            prev_root = self._safe_resolve(self.scan_ctx.root) if self.scan_ctx else None
            if prev_root is not None and root_resolved is not None and prev_root != root_resolved:
                self.manual_includes.clear()
                self._xcode_sources_root = None
                self._xcode_target_sources.clear()
                self._git_changed_cache.clear()

            if progress_cb:
                progress_cb(0.0, "Scanning files…")

            ctx: ScanContext
            token_workers_val = token_workers if token_workers is not None else getattr(ui.scan, "token_workers", 0)
            token_workers_val = int(token_workers_val or 0)

            saved_dirs = set(getattr(self.fp, "excluded_dirs", set()))
            saved_files = set(getattr(self.fp, "excluded_files", set()))
            orig_is_virtual_env = self.fp.is_virtual_env
            effective_dirs = set() if ignore_excludes else set(saved_dirs)
            effective_files = set() if ignore_excludes else set(saved_files)
            try:
                if ignore_excludes:
                    self.fp.is_virtual_env = lambda _dir_path: False
                effective_dirs = scan_service.compute_noise_exclusions(
                    effective_dirs, self._scan_extras(ui)
                )
                setattr(self.fp, "excluded_dirs", set(effective_dirs))
                setattr(self.fp, "excluded_files", set(effective_files))

                if ui.scan.use_git_files:
                    files = self.fp.git_files(root_path, include_untracked=ui.scan.git_include_untracked)
                    files = self._filter_git_files(files, root=root, ui=ui)
                    ctx = self.fp.scan_file_list(
                        root=root_path,
                        files=list(files),
                        sort_by=ui.scan.sort_by,
                        sort_descending=ui.scan.sort_desc,
                        progress_callback=progress_cb,
                    )
                else:
                    ctx = self.fp.scan_directory(
                        directory=root_path,
                        file_types=ui.file_types,
                        include_all=ui.scan.include_all,
                        ignore_binary=ui.scan.ignore_binary,
                        max_file_size_mb=ui.scan.max_file_size_mb,
                        sort_by=ui.scan.sort_by,
                        sort_descending=ui.scan.sort_desc,
                        token_workers=token_workers_val,
                        max_depth=ui.scan.max_depth,
                        follow_symlinks=ui.scan.follow_symlinks,
                        progress_callback=progress_cb,
                    )
            finally:
                self.fp.is_virtual_env = orig_is_virtual_env
                setattr(self.fp, "excluded_dirs", saved_dirs)
                setattr(self.fp, "excluded_files", saved_files)

            if ui.filters.always_include_docs and ui.filters.always_include_list:
                extra_files = find_always_include_files(
                    self.fp,
                    root_path,
                    ui.filters.always_include_list,
                    ignore_binary=ui.scan.ignore_binary,
                    max_file_size_mb=ui.scan.max_file_size_mb,
                    max_depth=ui.scan.max_depth,
                    follow_symlinks=ui.scan.follow_symlinks,
                )
                if extra_files:
                    ctx, _added = append_files_to_context(ctx, self.fp, root_path, extra_files)

            if self.manual_includes:
                ctx, _added = append_files_to_context(ctx, self.fp, root_path, list(self.manual_includes))

            self.scan_ctx = ctx
            self._apply_custom_excluded(custom_excluded)
            self._token_cache.clear()
            self._duplicate_hash_cache.clear()

            if progress_cb:
                progress_cb(100.0, f"Scanned {len(ctx.files)} files")
            return ctx

    def apply_filters(
        self,
        ui: UiState,
        *,
        sort_override: Optional[Tuple[str, bool]] = None,
        xcode_sources: set[str] | None = None,
        custom_excluded: Set[str] | None = None,
    ) -> FilterResult:
        """Apply filters to current scan_ctx and store view state."""
        with self._lock:
            if not self.scan_ctx:
                empty = FilterResult([], {}, set(), [], set(), {})
                self.visible_files = []
                self.folder_totals = {}
                self.dup_set = set()
                self.pattern_hits = set()
                return empty

            self._apply_custom_excluded(custom_excluded)

            git_changed = self._resolve_git_changed(ui)
            vendored = self._resolve_vendored(ui)

            if xcode_sources is None:
                xcode_sources = self._resolve_xcode_sources(ui)

            visible, folder_totals, dup_set, warnings, pattern_hits = scan_service.apply_filters(
                self.scan_ctx,
                ui,
                self.fp,
                git_changed,
                vendored,
                xcode_sources=xcode_sources,
                sort_override=sort_override,
                token_cache=self._token_cache,
                duplicate_hash_cache=self._duplicate_hash_cache,
                forced_includes=self.manual_includes,
            )

            self.visible_files = visible
            self.folder_totals = folder_totals
            self.dup_set = dup_set
            self.pattern_hits = pattern_hits

            content_mode_map = self.content_mode_map_for(str(m.abs_path) for m in visible)

            return FilterResult(
                visible_files=visible,
                folder_totals=folder_totals,
                duplicate_set=dup_set,
                warnings=warnings,
                pattern_hits=pattern_hits,
                content_mode_map=content_mode_map,
            )

    def totals_footer_text(
        self,
        ui: UiState,
        *,
        files: Sequence[FileMeta] | None = None,
    ) -> str:
        with self._lock:
            view = list(files) if files is not None else list(self.visible_files)
            content_mode_map = self.content_mode_map_for(str(m.abs_path) for m in view)
            return scan_service.merge_totals_footer_text(
                self.fp,
                ui,
                view,
                content_mode_map=content_mode_map,
            )

    # ------------------------------------------------------------------
    # Manual includes / custom exclusions
    # ------------------------------------------------------------------
    def add_manual_includes(self, paths: Iterable[str]) -> int:
        """Add manual include paths (absolute), returning how many new entries were added."""
        if paths is None:
            return 0
        with self._lock:
            before = len(self.manual_includes)
            for raw in paths:
                if not raw:
                    continue
                abs_path = self._normalise_abs(str(raw))
                if abs_path:
                    self.manual_includes.add(abs_path)
            return max(0, len(self.manual_includes) - before)

    def clear_manual_includes(self) -> None:
        with self._lock:
            self.manual_includes.clear()

    def manual_includes_snapshot(self) -> set[str]:
        with self._lock:
            return set(self.manual_includes)

    def add_manual_files(self, directory: str, files: Sequence[str]) -> List[FileMeta]:
        cleaned = [str(path) for path in files if path]
        if not cleaned:
            return []
        base_dir = directory or (str(self.scan_ctx.root) if self.scan_ctx else directory)

        with self._lock:
            ctx = self.scan_ctx
            added: List[FileMeta] = []
            custom_changed = False

            def _abs(path: str) -> str:
                return self._normalise_abs(path)

            def _lift(meta: FileMeta) -> None:
                nonlocal custom_changed
                if meta.excluded:
                    meta.excluded = False
                rel = meta.rel_path.as_posix()
                if rel in self.custom_excluded:
                    self.custom_excluded.discard(rel)
                    custom_changed = True

            if ctx is None:
                ctx, added = append_files_to_context(None, self.fp, base_dir, cleaned)
                for meta in added:
                    self.manual_includes.add(_abs(str(meta.abs_path)))
                    _lift(meta)
            else:
                for candidate in cleaned:
                    abs_path = _abs(candidate)
                    self.manual_includes.add(abs_path)
                    meta = next((m for m in ctx.files if _abs(str(m.abs_path)) == abs_path), None)
                    if meta is not None:
                        _lift(meta)
                        continue
                    ctx, new_entries = append_files_to_context(ctx, self.fp, base_dir, [candidate])
                    if new_entries:
                        meta = new_entries[-1]
                        _lift(meta)
                        added.extend(new_entries)
                        self.manual_includes.add(_abs(str(meta.abs_path)))

            self.scan_ctx = ctx
            if custom_changed:
                self._apply_custom_excluded()
            return list(added)

    def clear_custom_exclusions(self) -> bool:
        with self._lock:
            if not self.custom_excluded:
                return False
            self.custom_excluded.clear()
            self._apply_custom_excluded()
            return True

    def apply_keep_only(self, keep_rel: Iterable[str]) -> Tuple[int, int, int]:
        """Persistently exclude everything except the given root-relative paths.

        Returns a tuple of: (kept_count, excluded_count, invalid_count)
        where invalid_count counts input entries not present in the current scan.
        """
        keep_set: set[str] = set()
        for item in keep_rel:
            if not item:
                continue
            rel = str(item).strip().replace("\\", "/")
            if rel.startswith("./"):
                rel = rel[2:]
            if rel:
                keep_set.add(rel)

        with self._lock:
            if not self.scan_ctx or not self.scan_ctx.files:
                return 0, 0, len(keep_set)

            scanned_rel = {meta.rel_path.as_posix() for meta in self.scan_ctx.files}
            keep_effective = {rel for rel in keep_set if rel in scanned_rel}
            exclude_rel = scanned_rel - keep_effective

            self.custom_excluded = set(exclude_rel)
            self._apply_custom_excluded()

            invalid = max(0, len(keep_set) - len(keep_effective))
            return len(keep_effective), len(exclude_rel), invalid

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _apply_custom_excluded(self, excluded: Set[str] | None = None) -> None:
        if not self.scan_ctx or not self.scan_ctx.files:
            return
        excluded_lookup = set(self.custom_excluded if excluded is None else excluded)
        # Get the root for absolute path matching
        root_abs = ""
        try:
            root_abs = str(self.scan_ctx.root.resolve())
        except Exception:
            try:
                root_abs = str(self.scan_ctx.root)
            except Exception:
                pass

        for meta in self.scan_ctx.files:
            try:
                rel = meta.rel_path.as_posix()
                abs_path = str(meta.abs_path)
                # Check both relative and absolute paths for compatibility
                # (old configs use relative, new configs use absolute)
                is_excluded = (
                    rel in excluded_lookup or
                    abs_path in excluded_lookup or
                    (root_abs and (root_abs + "/" + rel) in excluded_lookup)
                )
                meta.excluded = is_excluded
            except Exception:
                continue

    def _resolve_git_changed(self, ui: UiState) -> Optional[Set[str]]:
        if not bool(getattr(ui.scan, "git_only_changed", False)):
            return None
        since = str(getattr(ui.scan, "git_since_ref", "") or "").strip()
        if not since:
            return None
        until = str(getattr(ui.scan, "git_until_ref", "HEAD") or "HEAD").strip() or "HEAD"
        if git_mod.get_git_root(str(ui.root_dir)) is None:
            return None
        key = (str(ui.root_dir), since, until)
        cached = self._git_changed_cache.get(key)
        if cached is None:
            cached = git_service.changed_set(str(ui.root_dir), since, until)
            self._git_changed_cache[key] = cached
        return cached

    def _resolve_vendored(self, ui: UiState) -> Optional[Set[str]]:
        if not bool(getattr(ui.scan, "exclude_vendored", False)):
            return None
        if git_mod.get_git_root(str(ui.root_dir)) is None:
            return None
        if not self.scan_ctx:
            return None
        rels = [meta.rel_path.as_posix() for meta in self.scan_ctx.files]
        return git_service.vendored_set(self.fp, str(ui.root_dir), rels)

    def _resolve_xcode_sources(self, ui: UiState) -> set[str] | None:
        target = ui.filters.xcode_target
        if not target or target == "(All)":
            return None
        root = self._safe_resolve(Path(ui.root_dir))
        if root is None:
            root = Path(ui.root_dir)
        if self._xcode_sources_root != root or not self._xcode_target_sources:
            try:
                mapping = self.fp.xcode_target_sources(str(root))
                self._xcode_target_sources = {k: set(v) for k, v in (mapping or {}).items()}
                self._xcode_sources_root = root
            except Exception:
                self._xcode_target_sources = {}
                self._xcode_sources_root = root
        return set(self._xcode_target_sources.get(target, set()))

    @staticmethod
    def _safe_resolve(path: Path) -> Path | None:
        try:
            return path.resolve()
        except Exception:
            return None

    @staticmethod
    def _scan_extras(ui: UiState) -> Dict[str, Any]:
        return {
            "exclude_node_modules": bool(getattr(ui.scan, "exclude_node_modules", True)),
            "exclude_ios_noise": bool(getattr(ui.scan, "exclude_ios_noise", True)),
            "exclude_python_noise": bool(getattr(ui.scan, "exclude_python_noise", False)),
            "exclude_java_gradle_noise": bool(getattr(ui.scan, "exclude_java_gradle_noise", False)),
            "exclude_web_build_noise": bool(getattr(ui.scan, "exclude_web_build_noise", False)),
        }

    def _filter_git_files(self, files: Sequence[str], *, root: Path, ui: UiState) -> List[str]:
        out: list[str] = []
        try:
            root_abs = str(root.resolve())
        except Exception:
            root_abs = str(root)

        excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(getattr(self.fp, "excluded_dirs", set()))
        excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(
            getattr(self.fp, "excluded_files", set())
        )
        excluded_dir_names = {
            d
            for d in excluded_dirs_rel
            if os.sep not in d and (os.altsep is None or os.altsep not in d)
        }
        excluded_dir_paths = {d for d in excluded_dirs_rel if d not in excluded_dir_names}

        for file in files:
            if not file:
                continue
            p = Path(file)
            try:
                resolved = p.resolve()
            except Exception:
                resolved = p
            if not resolved.exists() or not resolved.is_file():
                continue

            name_norm = norm_path(resolved.name)
            if name_norm in excluded_files_name:
                continue
            full_norm = norm_path(str(resolved))
            if full_norm in excluded_files_abs:
                continue

            try:
                rel = resolved.relative_to(Path(root_abs))
            except Exception:
                try:
                    rel = Path(os.path.relpath(str(resolved), root_abs))
                except Exception:
                    rel = Path(resolved.name)

            try:
                rel_norm = norm_path(str(rel))
            except Exception:
                rel_norm = norm_path(resolved.name)
            if rel_norm in excluded_files_rel:
                continue

            blocked = False
            prefixes = list(rel.parts[:-1])
            for idx in range(1, len(prefixes) + 1):
                prefix = os.path.normpath(os.path.join(*prefixes[:idx]))
                name_norm = norm_path(prefixes[idx - 1])
                if name_norm in excluded_dir_names:
                    blocked = True
                    break
                prefix_norm = norm_path(prefix)
                if prefix_norm in excluded_dir_paths:
                    blocked = True
                    break
                try:
                    abs_dir = os.path.join(root_abs, prefix)
                    abs_norm = norm_path(abs_dir)
                    if excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs):
                        blocked = True
                        break
                    if self.fp.is_virtual_env(abs_dir):
                        blocked = True
                        break
                except Exception:
                    pass
            if blocked:
                continue

            if ui.scan.max_depth and ui.scan.max_depth > 0:
                try:
                    parent = rel.parent
                    depth = 0 if parent in (Path("."), Path("")) else len(parent.parts)
                    if depth > ui.scan.max_depth:
                        continue
                except Exception:
                    pass

            if ui.scan.max_file_size_mb and ui.scan.max_file_size_mb > 0:
                try:
                    max_bytes = ui.scan.max_file_size_mb * 1024 * 1024
                    if resolved.stat().st_size > max_bytes:
                        continue
                except Exception:
                    pass

            if ui.scan.ignore_binary:
                try:
                    if self.fp.is_binary_file(str(resolved)):
                        continue
                except Exception:
                    continue

            if not ui.scan.include_all:
                ext = resolved.suffix.lower().lstrip(".")
                if not bool(ui.file_types.get(ext, False)):
                    continue

            out.append(str(resolved))

        return out
