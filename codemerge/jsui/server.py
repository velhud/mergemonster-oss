from __future__ import annotations

import asyncio
import copy
import concurrent.futures
import fnmatch
import importlib
import importlib.util
import json
import os
import secrets
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import StringIO
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlencode

try:
    from fastapi import Body, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
    from pydantic import BaseModel, ConfigDict
    from starlette.staticfiles import StaticFiles
    from starlette.types import Receive, Scope, Send
except Exception as exc:  # pragma: no cover - import guidance only
    raise RuntimeError(
        "FastAPI is required for the JS UI server. Install with:\n\n"
        "  pip install fastapi uvicorn\n\n"
        "Then run:\n\n"
        "  uvicorn codemerge.jsui.server:app --reload\n"
    ) from exc

from ..logger import LOG_DIR, get_logger
from ..core.controller import CoreController
from ..core.normalize import canonical_path, sanitize_pasted_paths
from ..filemerger.config import AppConfig
from ..filemerger.api import FileProcessor, _format_summary, _normalize_exclusion_entry
from ..filemerger.context_pack import ContextPackError, list_manifest_subsystems
from ..filemerger.exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)
from ..filemerger import merge_basic as merge_basic_mod
from ..filemerger.output import staged_output_directory
from ..filemerger.scan import discover_extensions
from .path_policy import (
    OUTPUT_MARKER,
    PathConflict,
    PathDenied,
    PathGrant,
    PathGrantKind,
    PathGrantPurpose,
    PathInvalid,
    PathPolicy,
    PathPolicyError,
)
from ..filemerger.services.dto import (
    FilterOptions,
    MergeStructure,
    OutputOptions,
    ProgressCb,
    ScanOptions,
    SummaryOptions,
    UiState,
)
from ..filemerger.services import diagnostics_service, merge_service, presets_service, scan_service, watch_service
from ..llm.accounting import provider_call_counter
from ..models import DEFAULT_SIZE_MAX, DEFAULT_TOKEN_MAX, FileMeta, ScanContext, Category, FileMode
from ..utils import open_file_in_default_app
from ..summarizer.shared.orchestrator import ensure_summaries, load_cached_summaries
from ..summarizer.shared.scope_scan import scan_aggregate_scope_files
from ..summarizer.shared.scope import build_manifest, filter_folder_metas, inventory_in_scope
from ..summarizer.router import (
    ensure_folder_summary,
    ensure_repo_summary,
    get_full_coverage_proof,
    render_folder_summary,
    render_repo_summary,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
APP_VERSION = "0.1.1"
CSRF_HEADER = "X-CodeMerge-CSRF"
CSRF_EXEMPT_PATHS = frozenset({"/api/health", "/api/session"})


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    SCAN = "scan"
    MERGE = "merge"
    MERGE_SPLIT_LINES = "merge_split_lines"
    MERGE_TOKEN_SPLIT = "merge_token_split"
    PRUNE = "prune"
    SUMMARIZE = "summarize"
    SUMMARIZE_FOLDER = "summarize_folder"
    SUMMARIZE_REPO = "summarize_repo"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class JobSnapshot:
    id: str
    type: str
    status: str
    progress: str
    progress_pct: int
    created_at: str | None
    completed_at: str | None
    result: Any | None
    error: str | None
    output_paths: tuple[str, ...]
    cancellable: bool = False
    cancel_supported: bool = False


class _AllowExtraModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class JobSnapshotResponse(_AllowExtraModel):
    id: str
    type: str
    status: str
    progress: str
    progress_pct: int
    created_at: str | None = None
    completed_at: str | None = None
    result: Any | None = None
    error: str | None = None
    output_paths: list[str] = []
    cancellable: bool = False
    cancel_supported: bool = False
    input_snapshot_id: str | None = None
    input_snapshot_root: str | None = None
    input_snapshot_created_at: str | None = None
    provider_preflight: dict[str, Any] | None = None
    provider_accounting: dict[str, Any] | None = None


class JobsListResponse(BaseModel):
    jobs: list[JobSnapshotResponse]
    count: int


class JobCancelResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    cancel_supported: bool
    message: str | None = None


class ProviderPreflightResponse(BaseModel):
    action: str
    requires_confirmation: bool
    confirmation_token: str | None = None
    scope: dict[str, Any]
    budget: dict[str, Any]
    provider_policy: dict[str, Any]
    risks: list[str]
    message: str


class WorkspaceSessionModel(_AllowExtraModel):
    version: int
    id: str | None = None
    created_at: str | None = None
    root: str | None = None
    config: dict[str, Any] = {}
    scan: dict[str, Any] = {}
    selection: dict[str, Any] = {}
    modes: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    provider_policy: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}


class WorkspaceSessionEnvelope(BaseModel):
    session: WorkspaceSessionModel


class WorkspaceSessionMutationResponse(BaseModel):
    ok: bool
    session: WorkspaceSessionModel
    diagnostics: dict[str, Any]


class ConfigEnvelope(_AllowExtraModel):
    config: dict[str, Any]
    updated: int | None = None


class PathAllowResponse(_AllowExtraModel):
    ok: bool
    path: str
    granted: bool
    purpose: str


class PathExistsResponse(_AllowExtraModel):
    exists: bool
    is_file: bool
    is_dir: bool


class PresetNamesResponse(_AllowExtraModel):
    names: list[str]


class ScanResponse(_AllowExtraModel):
    root: str
    files: list[dict[str, Any]]
    aggregate_tokens: int
    aggregate_bytes: int
    count: int


class OutputConcatResponse(_AllowExtraModel):
    text: str
    files: list[str]
    count: int


class OutputZipResponse(_AllowExtraModel):
    zip_path: str
    download_url: str


class FilePreviewResponse(_AllowExtraModel):
    text: str
    file_path: str
    size_bytes: int | None = None
    max_kb: int
    shown_kb: int
    truncated: bool


class PairResponse(_AllowExtraModel):
    ok: bool
    dest: str


class PairAiResponse(_AllowExtraModel):
    report: str
    output_path: str | None = None
    provider_accounting: dict[str, Any]


class RestoreResponse(_AllowExtraModel):
    ok: bool
    dest: str


@dataclass(frozen=True)
class JobInputSnapshot:
    id: str
    root: str | None
    config: dict[str, Any]
    scan_ctx: ScanContext | None
    per_file_modes: dict[str, FileMode]
    per_folder_modes: dict[str, str]
    created_at: str


class JobService:
    TERMINAL = {
        JobStatus.DONE.value,
        JobStatus.ERROR.value,
        JobStatus.CANCELLED.value,
    }

    def __init__(
        self,
        jobs: dict[str, dict],
        lock: threading.Lock,
        *,
        max_jobs: int = 500,
        max_running: int = 16,
        max_queued: int = 64,
        completed_ttl_seconds: float = 3600.0,
    ) -> None:
        self.jobs = jobs
        self.lock = lock
        self.max_jobs = max(1, int(max_jobs))
        self.max_running = max(1, int(max_running))
        self.max_queued = max(0, int(max_queued))
        self.completed_ttl_seconds = max(0.0, float(completed_ttl_seconds))
        self._running: set[str] = set()
        self._queued: list[tuple[str, Any, tuple[Any, ...]]] = []

    def _completed_age_seconds(self, job: dict, *, now: datetime) -> float | None:
        raw = job.get("completed_at")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            completed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=UTC)
        return max(0.0, (now - completed).total_seconds())

    def cleanup_locked(self) -> None:
        now = datetime.now(UTC)
        expired: list[str] = []
        terminal_ids: list[tuple[str, str]] = []
        for job_id, job in self.jobs.items():
            if str(job.get("status")) not in self.TERMINAL:
                continue
            completed_at = str(job.get("completed_at") or "")
            terminal_ids.append((completed_at, job_id))
            age = self._completed_age_seconds(job, now=now)
            if age is not None and age >= self.completed_ttl_seconds:
                expired.append(job_id)
        for job_id in expired:
            self.jobs.pop(job_id, None)
        if len(self.jobs) <= self.max_jobs:
            return
        for _completed_at, job_id in sorted(terminal_ids):
            if len(self.jobs) <= self.max_jobs:
                break
            self.jobs.pop(job_id, None)

    def _running_count_locked(self) -> int:
        legacy_running = {
            job_id
            for job_id, job in self.jobs.items()
            if str(job.get("status")) == JobStatus.RUNNING.value and job_id not in self._running
        }
        return len(self._running) + len(legacy_running)

    def _next_queued_launch_locked(self) -> tuple[str, Any, tuple[Any, ...]] | None:
        while self._queued and self._running_count_locked() < self.max_running:
            job_id, target, args = self._queued.pop(0)
            job = self.jobs.get(job_id)
            if job is None or str(job.get("status")) != JobStatus.QUEUED.value:
                continue
            job["status"] = JobStatus.RUNNING.value
            job["progress"] = "starting"
            job["progress_pct"] = 0
            job["cancellable"] = bool(job.get("running_cancellable", False))
            job["cancel_supported"] = bool(job.get("running_cancel_supported", False))
            self._running.add(job_id)
            return job_id, target, args
        return None

    def submit(
        self,
        job_type: JobType | str,
        target: Any,
        args: tuple[Any, ...],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        import uuid

        launch: tuple[str, Any, tuple[Any, ...]] | None = None
        with self.lock:
            self.cleanup_locked()
            if len(self.jobs) >= self.max_jobs:
                return None, "job history is full"
            job_id = uuid.uuid4().hex[:12]
            job_type_value = str(job_type.value if isinstance(job_type, JobType) else job_type)
            if self._running_count_locked() < self.max_running:
                status = JobStatus.RUNNING.value
                progress = "starting"
                cancellable = False
                cancel_supported = False
                self._running.add(job_id)
                launch = (job_id, target, args)
            else:
                queued_count = sum(1 for job in self.jobs.values() if str(job.get("status")) == JobStatus.QUEUED.value)
                if queued_count >= self.max_queued:
                    return None, "job queue is full"
                status = JobStatus.QUEUED.value
                progress = "queued"
                cancellable = True
                cancel_supported = True
                self._queued.append((job_id, target, args))
            job = {
                "id": job_id,
                "status": status,
                "type": job_type_value,
                "progress": progress,
                "progress_pct": 0,
                "created_at": _utc_now_iso(),
                "cancellable": cancellable,
                "cancel_supported": cancel_supported,
            }
            if metadata:
                job.update(metadata)
            self.jobs[job_id] = job
        if launch is not None:
            err = self._launch(*launch)
            if err is not None:
                return job_id, err
        return job_id, None

    def _launch(self, job_id: str, target: Any, args: tuple[Any, ...]) -> str | None:
        try:
            th = threading.Thread(target=self._run_wrapper, args=(job_id, target, args), daemon=True)
            th.start()
            return None
        except Exception as exc:
            with self.lock:
                job = self.jobs.get(job_id)
                if job is not None:
                    job["status"] = JobStatus.ERROR.value
                    job["error"] = str(exc)
                    job["completed_at"] = _utc_now_iso()
                    job["cancellable"] = False
                self._running.discard(job_id)
            return str(exc)

    def _run_wrapper(self, job_id: str, target: Any, args: tuple[Any, ...]) -> None:
        with provider_call_counter() as counter:
            try:
                if args:
                    target(args[0], job_id, *args[1:])
                else:
                    target(job_id)
            except Exception as exc:
                with self.lock:
                    job = self.jobs.get(job_id)
                    if job is not None and str(job.get("status")) not in self.TERMINAL:
                        job["status"] = JobStatus.ERROR.value
                        job["error"] = str(exc)
                        job["completed_at"] = _utc_now_iso()
                        job["cancellable"] = False
            finally:
                observed = counter.snapshot()
                with self.lock:
                    job = self.jobs.get(job_id)
                    if job is not None and "provider_accounting" in job:
                        job["provider_accounting"] = _merge_observed_provider_accounting(
                            job.get("provider_accounting"),
                            observed,
                        )
        launch: tuple[str, Any, tuple[Any, ...]] | None = None
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and str(job.get("status")) not in self.TERMINAL:
                job["status"] = JobStatus.DONE.value
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
                job["cancellable"] = False
            self._running.discard(job_id)
            launch = self._next_queued_launch_locked()
        if launch is not None:
            self._launch(*launch)

    def cancel(self, job_id: str) -> tuple[dict[str, Any] | None, str | None, int]:
        launch: tuple[str, Any, tuple[Any, ...]] | None = None
        with self.lock:
            self.cleanup_locked()
            job = self.jobs.get(job_id)
            if job is None:
                return None, "job not found", 404
            status = str(job.get("status") or "")
            if status in self.TERMINAL:
                return job, "job is already terminal", 409
            if status == JobStatus.QUEUED.value:
                self._queued = [queued for queued in self._queued if queued[0] != job_id]
                job["status"] = JobStatus.CANCELLED.value
                job["progress"] = "cancelled"
                job["progress_pct"] = 0
                job["completed_at"] = _utc_now_iso()
                job["cancellable"] = False
                job["cancel_supported"] = True
                launch = self._next_queued_launch_locked()
            else:
                job["cancellable"] = bool(job.get("cancellable", False) and job.get("cancel_supported", False))
                return job, "running job does not support cooperative cancellation", 409
        if launch is not None:
            self._launch(*launch)
        with self.lock:
            return self.jobs.get(job_id), None, 200


logger = get_logger(__name__)


@dataclass
class _ScanHostView:
    processor: FileProcessor
    excluded_dirs: set[str]
    excluded_files: set[str]

    def is_virtual_env(self, dir_path: str) -> bool:
        return self.processor.is_virtual_env(dir_path)

    def is_binary_file(self, file_path: str) -> bool:
        return self.processor.is_binary_file(file_path)

    def categorise(self, path: str, is_binary_hint: bool = False) -> Category:
        return self.processor.categorise(path, is_binary_hint=is_binary_hint)

    def file_token_count(self, path: str) -> int | None:
        return self.processor.file_token_count(path)

_MODULE_PATHS_LOGGED = False


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _copy_scan_context(scan_ctx: ScanContext | None) -> ScanContext | None:
    if scan_ctx is None:
        return None
    return ScanContext(
        root=Path(scan_ctx.root),
        files=list(scan_ctx.files),
        aggregate_tokens=int(getattr(scan_ctx, "aggregate_tokens", 0) or 0),
        aggregate_bytes=int(getattr(scan_ctx, "aggregate_bytes", 0) or 0),
        by_ext={str(k): list(v) for k, v in getattr(scan_ctx, "by_ext", {}).items()},
        by_category={k: list(v) for k, v in getattr(scan_ctx, "by_category", {}).items()},
    )


def _job_input_snapshot_id(root: str | None, cfg: dict[str, Any], scan_ctx: ScanContext | None) -> str:
    files = []
    if scan_ctx is not None:
        files = [
            {
                "path": str(meta.abs_path),
                "mtime": getattr(meta, "mtime", None),
                "size": getattr(meta, "size_bytes", None),
                "tokens": getattr(meta, "token_count", None),
            }
            for meta in list(getattr(scan_ctx, "files", []) or [])
        ]
    payload = {
        "root": root,
        "config": cfg,
        "scan_root": str(scan_ctx.root) if scan_ctx is not None else None,
        "files": files,
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _capture_job_input_snapshot(ctx: "ServerContext", root: Path | str | None = None) -> JobInputSnapshot:
    cfg = copy.deepcopy(ctx.config.get_config())
    scan_ctx = _copy_scan_context(ctx.core.scan_ctx)
    root_str = str(root) if root is not None else (str(scan_ctx.root) if scan_ctx is not None else None)
    return JobInputSnapshot(
        id=_job_input_snapshot_id(root_str, cfg, scan_ctx),
        root=root_str,
        config=cfg,
        scan_ctx=scan_ctx,
        per_file_modes=dict(ctx.core.per_file_modes),
        per_folder_modes=dict(ctx.core.per_folder_modes),
        created_at=_utc_now_iso(),
    )


def _job_snapshot_metadata(snapshot: JobInputSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    return {
        "input_snapshot_id": snapshot.id,
        "input_snapshot_root": snapshot.root,
        "input_snapshot_created_at": snapshot.created_at,
    }


def _log_module_paths_once() -> None:
    global _MODULE_PATHS_LOGGED
    if _MODULE_PATHS_LOGGED:
        return
    _MODULE_PATHS_LOGGED = True
    try:
        import sys
        import codemerge
        from codemerge.summarizer.quick import orchestrator as agg
        import codemerge.llm.summarize as sum_api
        from codemerge.summarizer.shared import orchestrator as orch

        logger.info("codemerge.__file__=%s", getattr(codemerge, "__file__", "unknown"))
        logger.info("codemerge.llm.summarize.__file__=%s", getattr(sum_api, "__file__", "unknown"))
        logger.info("codemerge.summarizer.quick.orchestrator.__file__=%s", getattr(agg, "__file__", "unknown"))
        logger.info("codemerge.summarizer.shared.orchestrator.__file__=%s", getattr(orch, "__file__", "unknown"))

        candidates: list[str] = []
        for entry in sys.path:
            if not entry:
                continue
            try:
                pkg_dir = os.path.join(entry, "codemerge")
            except Exception:
                continue
            if os.path.isdir(pkg_dir):
                candidates.append(pkg_dir)
        if len(candidates) > 1:
            logger.warning("Multiple codemerge locations on sys.path: %s", candidates)
        else:
            logger.info("codemerge sys.path locations: %s", candidates)
    except Exception:
        logger.exception("Failed to log module paths")


def _cleanup_stale_project_data(cfg: Dict[str, Any], new_root: Path) -> Dict[str, Any]:
    """
    Remove project-specific config data that doesn't belong to new_root.

    This prevents cross-contamination when switching between different projects
    that share the same config file. Cleans:
    - per_file_modes: remove entries for files not under new_root
    - custom_excluded: remove entries not under new_root (unless relative)
    - last_output: reset if not under new_root

    Returns a patch dict to apply to the config.
    """
    patch: Dict[str, Any] = {}
    new_root_str = str(new_root)

    # Clean per_file_modes - remove entries not under new_root
    per_file_modes = cfg.get("per_file_modes", {})
    if isinstance(per_file_modes, dict) and per_file_modes:
        cleaned_modes: Dict[str, str] = {}
        for path, mode in per_file_modes.items():
            if not isinstance(path, str):
                continue
            # Keep if path is under new root
            try:
                if path.startswith(new_root_str) or path.startswith("/"):
                    # Absolute path - check if under new root
                    if path.startswith(new_root_str):
                        cleaned_modes[path] = mode
                else:
                    # Relative path - keep it
                    cleaned_modes[path] = mode
            except Exception:
                continue
        if len(cleaned_modes) != len(per_file_modes):
            patch["per_file_modes"] = cleaned_modes
            logger.info(
                "Cleaned %d stale per_file_modes entries (from %d to %d)",
                len(per_file_modes) - len(cleaned_modes),
                len(per_file_modes),
                len(cleaned_modes),
            )

    # Clean custom_excluded - remove absolute paths not under new_root
    custom_excluded = cfg.get("custom_excluded", [])
    if isinstance(custom_excluded, list) and custom_excluded:
        cleaned_excluded: list[str] = []
        for entry in custom_excluded:
            if not isinstance(entry, str):
                continue
            # Keep relative paths (they apply to current root)
            if not entry.startswith("/"):
                cleaned_excluded.append(entry)
            # Keep absolute paths under new root
            elif entry.startswith(new_root_str):
                cleaned_excluded.append(entry)
        if len(cleaned_excluded) != len(custom_excluded):
            patch["custom_excluded"] = cleaned_excluded
            logger.info(
                "Cleaned %d stale custom_excluded entries",
                len(custom_excluded) - len(cleaned_excluded),
            )

    # Reset last_output if it points to a different project
    last_output = cfg.get("last_output", "")
    if isinstance(last_output, str) and last_output.startswith("/"):
        if not last_output.startswith(new_root_str):
            patch["last_output"] = ""
            logger.info("Reset last_output (pointed to different project)")

    return patch


def _ensure_config_for_path(path: str | None, *, ctx: "ServerContext" | None = None) -> None:
    if not path or ctx is None:
        return
    try:
        root = Path(path).expanduser()
    except Exception:
        return
    if root.is_file():
        root = root.parent
    try:
        root = root.resolve()
    except Exception:
        root = Path(os.path.abspath(str(root)))
    with ctx.config_lock:
        # Detect directory change and clean stale data
        cfg = ctx.config.get_config()
        old_directory = cfg.get("last_directory", "")
        new_directory = str(root)

        # Only clean if we're actually switching to a different root
        if old_directory and old_directory != new_directory:
            try:
                old_root = Path(old_directory).resolve()
                # Check if new root is NOT a subdirectory of old root and vice versa
                # (we only clean when switching between truly different projects)
                is_subdir = False
                try:
                    root.relative_to(old_root)
                    is_subdir = True
                except ValueError:
                    pass
                try:
                    old_root.relative_to(root)
                    is_subdir = True
                except ValueError:
                    pass

                if not is_subdir:
                    cleanup_patch = _cleanup_stale_project_data(cfg, root)
                    if cleanup_patch:
                        ctx.config.update_config(cleanup_patch)
                        # Also clear in-memory state
                        if "per_file_modes" in cleanup_patch:
                            ctx.core.per_file_modes = {
                                p: m for p, m in ctx.core.per_file_modes.items()
                                if str(p).startswith(new_directory)
                            }
                        if "custom_excluded" in cleanup_patch:
                            ctx.core.custom_excluded = set(cleanup_patch["custom_excluded"])
            except Exception:
                logger.exception("Failed to clean stale project data")

        try:
            ctx.config.update_config({"last_directory": str(root)})
        except Exception:
            pass


def _canonicalize_path(path: str) -> str:
    return canonical_path(path)


def _limit_groq_workers(cfg: Dict[str, Any], *, limit: int = 2) -> Dict[str, Any]:
    cfg_out = dict(cfg or {})
    cfg_out["groq_use_batch"] = False
    try:
        current = int(cfg_out.get("groq_max_workers", 0) or 0)
    except Exception:
        current = 0
    if current <= 0:
        cfg_out["groq_max_workers"] = limit
    else:
        cfg_out["groq_max_workers"] = min(current, limit)
    return cfg_out


class SummarizationManager:
    """Serialize Groq summarization work and dedupe in-flight jobs."""

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._inflight: dict[tuple, concurrent.futures.Future] = {}

    def submit(self, key: tuple, func: callable, *args, **kwargs) -> concurrent.futures.Future:
        with self._lock:
            existing = self._inflight.get(key)
            if existing:
                return existing
            fut = self._executor.submit(func, *args, **kwargs)
            self._inflight[key] = fut

        def _cleanup(done: concurrent.futures.Future) -> None:
            with self._lock:
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)

        # Register cleanup after releasing the lock so a fast-completing task
        # cannot re-enter _cleanup while submit() still holds _lock.
        fut.add_done_callback(_cleanup)
        return fut

    def run(self, key: tuple, func: callable, *args, **kwargs):
        return self.submit(key, func, *args, **kwargs).result()


def _file_summary_job_key(kind: str, root_abs: str, paths: Sequence[str], prompt_version: int) -> tuple:
    normalized = tuple(sorted(_canonicalize_path(p) for p in paths))
    return ("file_summary", kind, int(prompt_version or 0), root_abs, normalized)


def _paths_fingerprint(paths: Iterable[str]) -> str:
    normalized = sorted(_canonicalize_path(p) for p in paths)
    raw = "\n".join(normalized).encode("utf-8")
    return sha256(raw).hexdigest()


def _summary_mode_or_default(mode: str | None, *, default: str = "full") -> str:
    raw = str(mode or default).strip().lower()
    if raw in {"quick", "full"}:
        return raw
    raise ValueError("summary mode must be 'quick' or 'full'")


def _summary_job_runtime_contract(
    *,
    target_kind: str,
    mode: str | None,
    cfg: Dict[str, Any],
) -> tuple[str, str]:
    from ..llm.summarize import resolve_full_summary_model

    mode_norm = _summary_mode_or_default(mode, default="full")
    if mode_norm == "quick":
        from ..summarizer.quick import orchestrator as quick_orch

        runtime_fingerprint = quick_orch._quick_runtime_fingerprint(target_kind, cfg)
    else:
        from ..summarizer.full import orchestrator as full_orch

        runtime_fingerprint = full_orch._full_runtime_fingerprint(cfg)
    return resolve_full_summary_model(cfg), str(runtime_fingerprint or "")


def _folder_summary_job_key(
    folder_abs: str,
    root_abs: str,
    prompt_version: int,
    mode: str | None,
    *,
    model_name: str | None = None,
    runtime_fingerprint: str | None = None,
) -> tuple:
    return (
        "folder_summary",
        _summary_mode_or_default(mode, default="full"),
        int(prompt_version or 0),
        root_abs,
        _canonicalize_path(folder_abs),
        str(model_name or ""),
        str(runtime_fingerprint or ""),
    )


def _folder_summary_job_key_with_scope(
    folder_abs: str,
    root_abs: str,
    prompt_version: int,
    mode: str | None,
    *,
    model_name: str | None = None,
    runtime_fingerprint: str | None = None,
    paths: Iterable[str] | None = None,
) -> tuple:
    if paths is not None:
        return (
            "folder_summary",
            _summary_mode_or_default(mode, default="full"),
            int(prompt_version or 0),
            root_abs,
            _canonicalize_path(folder_abs),
            str(model_name or ""),
            str(runtime_fingerprint or ""),
            _paths_fingerprint(paths),
        )
    return _folder_summary_job_key(
        folder_abs,
        root_abs,
        prompt_version,
        mode,
        model_name=model_name,
        runtime_fingerprint=runtime_fingerprint,
    )


def _repo_summary_job_key(
    root_abs: str,
    prompt_version: int,
    mode: str | None,
    *,
    model_name: str | None = None,
    runtime_fingerprint: str | None = None,
    paths: Iterable[str] | None = None,
) -> tuple:
    if paths is not None:
        return (
            "repo_summary",
            _summary_mode_or_default(mode, default="full"),
            int(prompt_version or 0),
            root_abs,
            str(model_name or ""),
            str(runtime_fingerprint or ""),
            _paths_fingerprint(paths),
        )
    return (
        "repo_summary",
        _summary_mode_or_default(mode, default="full"),
        int(prompt_version or 0),
        root_abs,
        str(model_name or ""),
        str(runtime_fingerprint or ""),
    )


def _watch_summary_job_key(root_abs: str, plan: Dict[str, Any], prompt_version: int) -> tuple:
    items = tuple(sorted(( _canonicalize_path(p), str(m)) for p, m in (plan or {}).items()))
    return ("watch_tick", int(prompt_version or 0), root_abs, items)


_SUMMARY_PROVIDER_MODES = {
    "code_plus_short",
    "code_plus_long",
    "code_plus_short_plus_skeleton",
    "code_plus_long_plus_skeleton",
    "short_only",
    "long_only",
    "short_plus_skeleton",
    "long_plus_skeleton",
    "summary_short",
    "summary_long",
    "summary_short_plus_skeleton",
    "summary_long_plus_skeleton",
    "code_plus_summary_short_plus_skeleton",
    "code_plus_summary_long_plus_skeleton",
}


def _provider_policy_snapshot(cfg: Dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_model": cfg.get("groq_summary_model") or cfg.get("summary_model") or "",
        "summary_prompt_version": cfg.get("summary_prompt_version", 7),
        "auto_summarize_missing": cfg.get("auto_summarize_missing", True),
        "auto_refresh_summaries": cfg.get("auto_refresh_summaries", True),
        "groq_use_batch": cfg.get("groq_use_batch", False),
        "groq_max_workers": cfg.get("groq_max_workers", 0),
        "prune_engine": cfg.get("prune_engine", "local"),
        "prune_goal_llm_enabled": cfg.get("prune_goal_llm_enabled", True),
        "prune_goal_llm_span_select": cfg.get("prune_goal_llm_span_select", True),
        "enable_embeddings": cfg.get("enable_embeddings", True),
        "embedding_provider": cfg.get("embedding_provider", "groq"),
        "retrieval_backend": cfg.get("retrieval_backend", "embeddings"),
    }


def _estimate_provider_budget(ctx: "ServerContext", paths: Iterable[str] | None) -> dict[str, Any]:
    unique = []
    seen: set[str] = set()
    for raw in paths or []:
        if not isinstance(raw, str) or not raw:
            continue
        try:
            path = _canonicalize_path(raw)
        except Exception:
            path = str(raw)
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)

    scan_tokens: dict[str, int] = {}
    scan_bytes: dict[str, int] = {}
    scan_ctx = ctx.core.scan_ctx
    if scan_ctx is not None:
        for meta in getattr(scan_ctx, "files", []) or []:
            key = _canonicalize_path(str(meta.abs_path))
            scan_tokens[key] = int(getattr(meta, "token_count", 0) or 0)
            scan_bytes[key] = int(getattr(meta, "size_bytes", 0) or 0)

    tokens = 0
    bytes_total = 0
    for path in unique:
        if path in scan_tokens:
            tokens += scan_tokens[path]
            bytes_total += scan_bytes.get(path, 0)
            continue
        try:
            bytes_total += int(Path(path).stat().st_size)
        except Exception:
            pass
        try:
            tokens += int(ctx.processor.file_token_count(path) or 0)
        except Exception:
            pass
    return {
        "estimated_files": len(unique),
        "estimated_tokens": tokens,
        "estimated_bytes": bytes_total,
    }


def _prune_provider_risks(cfg: Dict[str, Any]) -> list[str]:
    risks: list[str] = []
    if _safe_bool(cfg.get("prune_goal_llm_span_select", True), True):
        risks.append("LLM span selection may call a chat/completions provider.")
    if _safe_bool(cfg.get("prune_goal_llm_enabled", True), True):
        risks.append("LLM goal interpretation may call a chat/completions provider.")
    embed_provider = str(cfg.get("embedding_provider", "groq") or "").strip().lower()
    retrieval_backend = str(cfg.get("retrieval_backend", "embeddings") or "").strip().lower()
    if _safe_bool(cfg.get("enable_embeddings", True), True) and embed_provider != "none" and retrieval_backend == "embeddings":
        risks.append(f"Embedding retrieval may call the {embed_provider or 'configured'} provider.")
    engine = str(cfg.get("prune_engine", "local") or "").strip().lower()
    if engine not in {"", "local", "keywords"}:
        risks.append(f"Prune engine {engine!r} may use provider-backed ranking.")
    if str(cfg.get("prune_ranking_mode") or "").lower() == "hybrid_rerank":
        risks.append("Hybrid reranking may call OpenAI.")
    if _safe_bool(cfg.get("prune_execute_workspace", False)):
        risks.append("Execution-based verification may send harness and trace data to Groq.")
    return risks


def _provider_preflight_decision(
    ctx: "ServerContext",
    action: str,
    *,
    cfg: Dict[str, Any] | None = None,
    root: str | None = None,
    target: str | None = None,
    paths: Iterable[str] | None = None,
    requires_confirmation: bool,
    risks: Iterable[str] | None = None,
    estimated_provider_calls: int | None = None,
) -> dict[str, Any]:
    cfg = dict(cfg or ctx.config.get_config())
    budget = _estimate_provider_budget(ctx, paths)
    file_count = int(budget.get("estimated_files", 0) or 0)
    if estimated_provider_calls is None:
        estimated_provider_calls = max(1, file_count) if requires_confirmation else 0
    budget["estimated_provider_calls"] = int(max(0, estimated_provider_calls))
    scope = {
        "root": str(root or cfg.get("last_directory") or ""),
        "target": str(target or ""),
        "path_count": file_count,
    }
    provider_policy = _provider_policy_snapshot(cfg)
    risk_list = [str(item) for item in (risks or []) if str(item).strip()]
    seed = {
        "action": action,
        "scope": scope,
        "budget": budget,
        "provider_policy": provider_policy,
        "risks": risk_list,
    }
    token = None
    if requires_confirmation:
        raw = json.dumps(seed, sort_keys=True, default=str, separators=(",", ":"))
        token = sha256((raw + ":" + ctx.csrf_token).encode("utf-8")).hexdigest()[:24]
    message = "Provider-backed run requires explicit confirmation." if requires_confirmation else "No provider-backed run is expected."
    return {
        "action": action,
        "requires_confirmation": bool(requires_confirmation),
        "confirmation_token": token,
        "scope": scope,
        "budget": budget,
        "provider_policy": provider_policy,
        "risks": risk_list,
        "message": message,
    }


def _provider_accounting_from_preflight(preflight: dict[str, Any]) -> dict[str, Any]:
    budget = preflight.get("budget") if isinstance(preflight, dict) else {}
    return {
        "estimated_provider_calls": int(dict(budget or {}).get("estimated_provider_calls") or 0),
        "observed_provider_calls": None,
        "confirmation_token": preflight.get("confirmation_token"),
        "requires_confirmation": bool(preflight.get("requires_confirmation")),
    }


def _merge_observed_provider_accounting(existing: object, observed: dict[str, object]) -> dict[str, object]:
    out = dict(existing) if isinstance(existing, dict) else {}
    for key, value in observed.items():
        out[key] = value
    return out


def _set_job_observed_provider_accounting(ctx: "ServerContext", job_id: str, observed: dict[str, object]) -> None:
    with ctx.jobs_lock:
        job = ctx.jobs.get(job_id)
        if job is not None:
            job["provider_accounting"] = _merge_observed_provider_accounting(
                job.get("provider_accounting"),
                observed,
            )


def _provider_job_metadata(preflight: dict[str, Any]) -> dict[str, Any]:
    if not preflight or not preflight.get("requires_confirmation"):
        return {}
    return {
        "provider_preflight": preflight,
        "provider_accounting": _provider_accounting_from_preflight(preflight),
    }


def _provider_confirmation_response(preflight: dict[str, Any], payload: Dict[str, Any]) -> JSONResponse | None:
    if not preflight.get("requires_confirmation"):
        return None
    confirmed = _safe_bool(payload.get("provider_confirmed", False), False)
    token = payload.get("provider_confirmation_token") or payload.get("provider_preflight_token")
    if confirmed and isinstance(token, str) and secrets.compare_digest(token, str(preflight.get("confirmation_token") or "")):
        return None
    return JSONResponse(
        {
            "error": "provider confirmation required",
            "preflight": preflight,
        },
        status_code=428,
    )


def _summary_provider_preflight(
    ctx: "ServerContext",
    action: str,
    *,
    cfg: Dict[str, Any],
    root: str | None,
    target: str | None,
    paths: Iterable[str] | None,
    mode: str | None = None,
    content_mode: str | None = None,
) -> dict[str, Any]:
    requested_mode = str(content_mode or mode or "").strip().lower()
    requires = requested_mode in _SUMMARY_PROVIDER_MODES or action in {"summarize_folder", "summarize_repo", "watch_tick"}
    risks = []
    if requires:
        risks.append("Missing or stale summaries may call a summary provider.")
        if _safe_bool(cfg.get("groq_use_batch", False), False):
            risks.append("Batch summarization is enabled in provider policy.")
    file_count = len(list(paths or []))
    calls = max(1, file_count) if action == "summarize" else max(1, min(file_count or 1, 50))
    return _provider_preflight_decision(
        ctx,
        action,
        cfg=cfg,
        root=root,
        target=target,
        paths=paths,
        requires_confirmation=requires,
        risks=risks,
        estimated_provider_calls=calls if requires else 0,
    )


def _merge_provider_preflight(
    ctx: "ServerContext",
    *,
    cfg: Dict[str, Any],
    base: Path,
    files_abs: list[str],
    payload: Dict[str, Any],
) -> dict[str, Any]:
    ctx.core.configure(cfg)
    content_override = None
    if "content_mode" in payload:
        content_override = _normalize_content_mode(payload.get("content_mode"), default="code_only")
    plan = ctx.core.build_plan(files_abs, default_override=content_override)
    summary_modes = {
        FileMode.SUMMARY_SHORT,
        FileMode.SUMMARY_LONG,
        FileMode.CODE_PLUS_SHORT,
        FileMode.CODE_PLUS_LONG,
        FileMode.SUMMARY_SHORT_PLUS_SKELETON,
        FileMode.SUMMARY_LONG_PLUS_SKELETON,
        FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
        FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
    }
    provider_paths = [path for path, mode in plan.items() if mode in summary_modes]
    include_repo = payload.get("include_repo_summary")
    if include_repo is None:
        include_repo = bool(cfg.get("include_repo_summary_in_merge", False))
    folder_modes = ctx.core.folder_modes_snapshot()
    folder_targets = [p for p, m in folder_modes.items() if str(m or "").strip().upper().startswith("FOLDER_SUMMARY")]
    requires = bool(provider_paths or include_repo or folder_targets)
    risks: list[str] = []
    if provider_paths:
        risks.append("Merge includes file summary modes that may refresh missing or stale summaries.")
    if include_repo:
        risks.append("Merge includes a repository summary block.")
    if folder_targets:
        risks.append("Merge includes folder summary blocks.")
    scope_paths = provider_paths or files_abs
    return _provider_preflight_decision(
        ctx,
        "merge",
        cfg=cfg,
        root=str(base),
        target=str(base),
        paths=scope_paths,
        requires_confirmation=requires,
        risks=risks,
        estimated_provider_calls=max(1, len(scope_paths)) if requires else 0,
    )

class ServerContext:
    def __init__(self) -> None:
        _log_module_paths_once()
        config_path = os.environ.get("CODEMERGE_CONFIG_PATH") or os.environ.get("CODEMERGE_CONFIG")
        self.config = AppConfig(config_path) if config_path else AppConfig()
        self.processor = FileProcessor()
        self.core = CoreController(file_processor=self.processor)
        self.jobs: dict[str, dict] = {}
        self.summaries: dict[str, Optional[str]] = {}
        self.path_grants: list[PathGrant] = []
        self.csrf_token = secrets.token_urlsafe(32)
        self.summarization_manager = SummarizationManager(max_workers=2)

        self.config_lock = threading.Lock()
        self.jobs_lock = threading.Lock()
        self.job_service = JobService(self.jobs, self.jobs_lock)
        self.core.configure(self.config.get_config())

    def cleanup(self) -> None:
        try:
            self.processor._token_cache.close()
            logger.info("Closed TokenCache connection")
        except Exception:
            logger.exception("Failed to close TokenCache connection")


GLOBAL_CTX: ServerContext | None = None
_LAST_PYTEST_TEST: str | None = None


def _init_global_ctx() -> ServerContext:
    global GLOBAL_CTX
    global _LAST_PYTEST_TEST
    current_test = os.environ.get("PYTEST_CURRENT_TEST")
    if current_test:
        if _LAST_PYTEST_TEST != current_test:
            GLOBAL_CTX = ServerContext()
            _LAST_PYTEST_TEST = current_test
        return GLOBAL_CTX
    if GLOBAL_CTX is None:
        GLOBAL_CTX = ServerContext()
    return GLOBAL_CTX


def _require_ctx() -> ServerContext:
    return _init_global_ctx()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global GLOBAL_CTX
    GLOBAL_CTX = ServerContext()
    app.state.ctx = GLOBAL_CTX
    try:
        yield
    finally:
        try:
            app.state.ctx.cleanup()
        finally:
            GLOBAL_CTX = None


app = FastAPI(title="MergeMonster", version=APP_VERSION, lifespan=lifespan)


async def desktop_app(scope: Scope, receive: Receive, send: Send) -> None:
    await app({**scope, "mergemonster.desktop": True}, receive, send)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _csrf_required_for_request(request: Request) -> bool:
    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    path = request.url.path
    if not path.startswith("/api/") or path in CSRF_EXEMPT_PATHS:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("CODEMERGE_REQUIRE_CSRF") != "1":
        return False
    return True


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    # Reject DNS rebinding and cross-origin browser requests before returning
    # either repository metadata or the local session token.
    allowed_hosts = {"127.0.0.1", "::1", "localhost"}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        allowed_hosts.add("testserver")
    if request.url.hostname not in allowed_hosts:
        return JSONResponse({"error": "invalid local host"}, status_code=400)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"error": "cross-origin request"}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"error": "cross-site request"}, status_code=403)
    if _csrf_required_for_request(request):
        ctx = _require_ctx()
        token = request.headers.get(CSRF_HEADER)
        if not token:
            return JSONResponse({"error": "missing CSRF token"}, status_code=403)
        if not secrets.compare_digest(token, ctx.csrf_token):
            return JSONResponse({"error": "invalid CSRF token"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path == "/" or request.url.path.startswith("/panel/"):
        # pywebview compiles its native bridge methods at runtime.
        bridge_eval = " 'unsafe-eval'" if request.scope.get("mergemonster.desktop") else ""
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self'{bridge_eval}; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
    return response


@app.get("/", response_model=None)
def index():
    """Serve the main HTML page or redirect to /api/health if missing."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return RedirectResponse(url="/api/health")


@app.get("/api/health")
def health() -> JSONResponse:
    """Basic health endpoint used by the frontend to verify server readiness."""
    payload = {
        "status": "ok",
        "ui": "jsui",
        "version": APP_VERSION,
        "time": _utc_now_iso(),
    }
    return JSONResponse(payload)


@app.get("/api/session")
def get_session() -> JSONResponse:
    ctx = _require_ctx()
    return JSONResponse(
        {
            "csrf_token": ctx.csrf_token,
            "csrf_header": CSRF_HEADER,
            "version": APP_VERSION,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/panel/{name}", response_model=None)
def panel(name: str):
    """Serve the same index for sub-windows; JS decides which section to show."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return RedirectResponse(url="/api/health")


# ----------------- Config endpoints -----------------
def _manual_lookup_snapshot() -> set[str]:
    ctx = _require_ctx()
    return ctx.core.manual_includes_snapshot()

def _grant_path(
    p: str | Path,
    *,
    purpose: PathGrantPurpose,
    kind: PathGrantKind | None = None,
    root: str | Path | None = None,
    expires_at: str | None = None,
) -> None:
    ctx = _require_ctx()
    try:
        path = Path(p).resolve(strict=False)
        grant_kind = kind or (PathGrantKind.FOLDER if path.is_dir() else PathGrantKind.FILE)
        grant = PathGrant(
            path=path,
            purpose=purpose,
            kind=grant_kind,
            root=root,
            created_at=_utc_now_iso(),
            expires_at=expires_at,
        )
    except Exception:
        return
    ctx.path_grants = [existing for existing in ctx.path_grants if existing != grant and not existing.is_expired()]
    ctx.path_grants.append(grant)


def _allow_path(p: str | Path) -> None:
    _grant_path(p, purpose=PathGrantPurpose.GENERATED_OUTPUT)


def _download_token_for_path(path: str | Path, ctx: "ServerContext" | None = None) -> str:
    srv = ctx or _require_ctx()
    raw = str(Path(path).resolve(strict=False))
    return sha256((f"download:{raw}:{srv.csrf_token}").encode("utf-8")).hexdigest()[:24]


def _download_url_for_path(path: str | Path, ctx: "ServerContext" | None = None) -> str:
    raw = str(Path(path).resolve(strict=False))
    return "/download?" + urlencode({"path": raw, "download_token": _download_token_for_path(raw, ctx=ctx)})


def _download_token_valid(path: str | Path, token: object, ctx: "ServerContext" | None = None) -> bool:
    srv = ctx or _require_ctx()
    if not isinstance(token, str) or not token:
        return False
    return secrets.compare_digest(token, srv.csrf_token) or secrets.compare_digest(
        token,
        _download_token_for_path(path, ctx=srv),
    )


def _path_policy_for(
    root: str | Path,
    *,
    ctx: "ServerContext" | None = None,
    allow_external_output: bool = False,
    allow_external_browse: bool = False,
) -> PathPolicy:
    srv = ctx or _require_ctx()
    return PathPolicy(
        root,
        allow_external_output=allow_external_output,
        allow_external_browse=allow_external_browse,
        explicit_user_paths=frozenset(_manual_lookup_snapshot()),
        grants=tuple(srv.path_grants),
    )


def _path_error_response(exc: PathPolicyError) -> JSONResponse:
    if isinstance(exc, PathDenied):
        return JSONResponse({"error": str(exc)}, status_code=403)
    if isinstance(exc, PathInvalid):
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"error": str(exc)}, status_code=409)


def _current_project_root(ctx: "ServerContext") -> Path | None:
    raw = ctx.config.get_config().get("last_directory")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        root = Path(raw).expanduser().resolve(strict=False)
    except Exception:
        return None
    if not root.exists() or not root.is_dir():
        return None
    return root


def _resolve_current_root_folder(
    raw: object,
    ctx: "ServerContext",
    *,
    field_name: str = "path",
    default_to_current: bool = False,
    allow_initial_root: bool = False,
) -> tuple[Path | None, JSONResponse | None]:
    root = _current_project_root(ctx)
    value = raw
    if root is not None and (not isinstance(value, str) or not value.strip()) and default_to_current:
        value = str(root)
    if not isinstance(value, str) or not value.strip():
        return None, JSONResponse({"error": f"{field_name} must be a non-empty string"}, status_code=400)
    if root is None:
        if not allow_initial_root:
            return None, JSONResponse({"error": "root path is missing; run scan first"}, status_code=409)
        try:
            initial = Path(value).expanduser().resolve(strict=False)
        except Exception:
            return None, JSONResponse({"error": "invalid path"}, status_code=400)
        if not initial.exists() or not initial.is_dir():
            return None, JSONResponse({"error": f"input folder does not exist: {initial}"}, status_code=400)
        return initial, None
    try:
        resolved = _path_policy_for(root, ctx=ctx).resolve_input_folder(value)
    except PathPolicyError as exc:
        return None, _path_error_response(exc)
    return resolved.path, None


def _is_allowed_download(p: Path) -> bool:
    ctx = _require_ctx()
    try:
        rp = p.resolve()
    except Exception:
        return False
    try:
        base_raw = ctx.config.get_config().get("last_directory", "") or "."
        policy = _path_policy_for(base_raw, ctx=ctx)
        return policy.can_download(rp)
    except Exception:
        pass
    # Allow under project root, or explicitly generated-output grants.
    allowed_by_root = False
    try:
        base = Path(ctx.config.get_config().get("last_directory", "") or ".").resolve()
        _ = rp.relative_to(base)
        allowed_by_root = True
    except Exception:
        allowed_by_root = False
    for grant in list(ctx.path_grants):
        try:
            if grant.matches(rp, PathGrantPurpose.GENERATED_OUTPUT):
                return True
        except Exception:
            continue
    return allowed_by_root


def _default_always_include_docs_list() -> list[str]:
    try:
        base = AppConfig()._load_default_config()
        raw = base.get("always_include_docs_list", [])
        if isinstance(raw, list):
            return [str(x) for x in raw if isinstance(x, str) and x]
    except Exception:
        pass
    return []


def _config_snapshot() -> Dict[str, Any]:
    cfg = _require_ctx().config.get_config()
    # Expose a minimal, safe subset for the UI.
    return {
        "last_directory": cfg.get("last_directory", ""),
        "last_output": cfg.get("last_output", ""),
        "selected_preset": cfg.get("selected_preset", ""),
        "last_preset": cfg.get("last_preset", ""),
        "view_mode": cfg.get("view_mode", "hierarchy"),
        "file_types": cfg.get("file_types", {}),
        "category_mask": cfg.get("category_mask", {}),
        "last_filters": cfg.get("last_filters", {}),
        "sort_by": cfg.get("sort_by", "name"),
        "sort_descending": cfg.get("sort_descending", False),
        "show_totals_columns": cfg.get("show_totals_columns", False),
        "show_density_column": cfg.get("show_density_column", False),
        "show_bytes_footer": cfg.get("show_bytes_footer", True),
        # Filter panel / scan defaults (persisted in config).
        "date_last_n_days": cfg.get("date_last_n_days", 0),
        "always_include_docs": cfg.get("always_include_docs", True),
        "name_filter_glob": cfg.get("name_filter_glob", False),
        "content_search": cfg.get("content_search", ""),
        "content_regex": cfg.get("content_regex", False),
        "content_case_sensitive": cfg.get("content_case_sensitive", False),
        "content_max_kb": cfg.get("content_max_kb", 256),
        "duplicate_hash_max_kb": cfg.get("duplicate_hash_max_kb", 256),
        "content_hide_duplicates": cfg.get("content_hide_duplicates", False),
        "content_mark_duplicates": cfg.get("content_mark_duplicates", True),
        "apply_token_filters_to_transforms": cfg.get("apply_token_filters_to_transforms", False),
        "ignore_binary_files": cfg.get("ignore_binary_files", False),
        "max_file_size_mb": cfg.get("max_file_size_mb", 0.0),
        "follow_symlinks": cfg.get("follow_symlinks", False),
        "max_walk_depth": cfg.get("max_walk_depth", 0),
        "exclude_node_modules": cfg.get("exclude_node_modules", True),
        "exclude_ios_noise": cfg.get("exclude_ios_noise", True),
        "exclude_python_noise": cfg.get("exclude_python_noise", False),
        "exclude_java_gradle_noise": cfg.get("exclude_java_gradle_noise", False),
        "exclude_web_build_noise": cfg.get("exclude_web_build_noise", False),
        "use_git_files": cfg.get("use_git_files", False),
        "git_include_untracked": cfg.get("git_include_untracked", False),
        "git_only_changed": cfg.get("git_only_changed", False),
        "git_since_ref": cfg.get("git_since_ref", ""),
        "git_until_ref": cfg.get("git_until_ref", "HEAD"),
        "exclude_vendored": cfg.get("exclude_vendored", False),
        "watch_enabled": cfg.get("watch_enabled", False),
        "watch_interval_sec": cfg.get("watch_interval_sec", 3),
        "watch_sample_limit": cfg.get("watch_sample_limit", 3000),
        # Prune defaults
        "prune_output_path": cfg.get("prune_output_path", ""),
        "prune_ranking_mode": cfg.get("prune_ranking_mode", "hybrid"),
        "prune_engine": cfg.get("prune_engine", "local"),
        "prune_neighbors_mode": cfg.get("prune_neighbors_mode", "same_package"),
        "prune_mode": cfg.get("prune_mode", "compact"),
        "prune_token_budget": cfg.get("prune_token_budget", 45_000),
        "prune_dry_run": cfg.get("prune_dry_run", False),
        "prune_goal_llm_enabled": cfg.get("prune_goal_llm_enabled", True),
        "prune_goal_llm_span_select": cfg.get("prune_goal_llm_span_select", True),
        "prune_neighbor_max": cfg.get("prune_neighbor_max", 80),
        "prune_exclude_tests": cfg.get("prune_exclude_tests", True),
        "prune_downrank_noise": cfg.get("prune_downrank_noise", True),
        "prune_noise_dirs": cfg.get("prune_noise_dirs", []),
        "prune_top_k": cfg.get("prune_top_k", 200),
        "prune_keep_neighbors": cfg.get("prune_keep_neighbors", True),
        "prune_max_repairs": cfg.get("prune_max_repairs", 3),
        "prune_span_to_file_threshold": cfg.get("prune_span_to_file_threshold", 0),
        "prune_keep_class_for_method": cfg.get("prune_keep_class_for_method", True),
        "prune_core_budget_tokens": cfg.get("prune_core_budget_tokens", 0),
        "prune_repair_budget_tokens": cfg.get("prune_repair_budget_tokens", 0),
        "prune_repair_max_spans_per_module": cfg.get("prune_repair_max_spans_per_module", 50),
        "prune_repair_prefer_spans": cfg.get("prune_repair_prefer_spans", True),
        "include_repo_summary_in_merge": cfg.get("include_repo_summary_in_merge", False),
        "include_repo_summary_mode": cfg.get("include_repo_summary_mode", "quick"),
    }


def _serialise_file_modes(modes: dict[str, FileMode]) -> dict[str, str]:
    return {str(path): mode.name if isinstance(mode, FileMode) else str(mode) for path, mode in modes.items()}


def _workspace_session_path_list(values: object, root: Path | None, diagnostics: dict[str, list[str]]) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    root_resolved: Path | None = None
    if root is not None:
        try:
            root_resolved = root.resolve()
        except Exception:
            root_resolved = root
    for item in values:
        if not isinstance(item, str) or not item.strip():
            continue
        p = Path(item)
        if not p.is_absolute() and root_resolved is not None:
            p = root_resolved / p
        try:
            resolved = p.resolve(strict=False)
        except Exception:
            diagnostics.setdefault("invalid_paths", []).append(item)
            continue
        if root_resolved is not None:
            try:
                resolved.relative_to(root_resolved)
            except Exception:
                diagnostics.setdefault("out_of_root_paths", []).append(str(resolved))
                continue
        resolved_str = str(resolved)
        if not resolved.exists():
            diagnostics.setdefault("missing_paths", []).append(resolved_str)
        if resolved_str not in seen:
            seen.add(resolved_str)
            out.append(resolved_str)
    return out


def _workspace_session_diagnostics(session: dict[str, Any]) -> dict[str, Any]:
    diagnostics: dict[str, list[str] | bool | str] = {
        "missing_paths": [],
        "out_of_root_paths": [],
        "invalid_paths": [],
    }
    root_raw = session.get("root")
    root: Path | None = None
    if isinstance(root_raw, str) and root_raw:
        root = Path(root_raw)
        try:
            root_resolved = root.resolve(strict=False)
        except Exception:
            root_resolved = root
        if not root_resolved.exists() or not root_resolved.is_dir():
            diagnostics["missing_root"] = str(root_resolved)
        root = root_resolved
    selection = session.get("selection") if isinstance(session.get("selection"), dict) else {}
    for key in ("selected_paths", "hidden_paths", "visible_paths"):
        _workspace_session_path_list(selection.get(key), root, diagnostics)  # type: ignore[arg-type]
    return diagnostics


def _current_workspace_session(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = _require_ctx()
    cfg = _config_snapshot()
    root_raw = cfg.get("last_directory") if isinstance(cfg.get("last_directory"), str) else ""
    root = Path(root_raw).resolve(strict=False) if root_raw else None
    visible_paths = [str(meta.abs_path) for meta in list(getattr(ctx.core, "visible_files", []) or [])]
    selected_raw = payload.get("selected_paths") if isinstance(payload, dict) else None
    hidden_raw = payload.get("hidden_paths") if isinstance(payload, dict) else None
    visible_raw = payload.get("visible_paths") if isinstance(payload, dict) else None
    diagnostics: dict[str, list[str]] = {}
    selected_paths = _workspace_session_path_list(selected_raw, root, diagnostics)
    hidden_paths = _workspace_session_path_list(hidden_raw, root, diagnostics)
    visible_override = _workspace_session_path_list(visible_raw, root, diagnostics)
    session = {
        "version": 1,
        "created_at": _utc_now_iso(),
        "root": str(root) if root is not None else root_raw,
        "config": copy.deepcopy(cfg),
        "scan": {
            "root": str(ctx.core.scan_ctx.root) if ctx.core.scan_ctx is not None else None,
            "file_count": len(getattr(ctx.core.scan_ctx, "files", []) or []) if ctx.core.scan_ctx is not None else 0,
            "visible_count": len(visible_override or visible_paths),
            "visible_paths": visible_override or visible_paths,
        },
        "selection": {
            "selected_paths": selected_paths,
            "hidden_paths": hidden_paths,
            "visible_paths": visible_override or visible_paths,
        },
        "modes": {
            "per_file_modes": _serialise_file_modes(ctx.core.per_file_modes),
            "per_folder_modes": dict(ctx.core.per_folder_modes),
        },
        "outputs": {
            "last_output": cfg.get("last_output", ""),
            "prune_output_path": cfg.get("prune_output_path", ""),
        },
        "provider_policy": {
            "prune_engine": cfg.get("prune_engine", "local"),
            "groq_use_batch": cfg.get("groq_use_batch", False),
            "groq_max_workers": cfg.get("groq_max_workers", 0),
            "strict_summary_required": cfg.get("strict_summary_required", False),
        },
    }
    session["id"] = sha256(json.dumps(session, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    session["diagnostics"] = diagnostics
    return session


def _normalise_workspace_session(raw: object) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    if isinstance(raw, dict) and isinstance(raw.get("session"), dict):
        raw = raw.get("session")
    if not isinstance(raw, dict):
        return None, JSONResponse({"error": "session must be an object"}, status_code=400)
    session = copy.deepcopy(raw)
    try:
        version = int(session.get("version", 1) or 1)
    except Exception:
        return None, JSONResponse({"error": "session.version must be an integer"}, status_code=400)
    if version != 1:
        return None, JSONResponse({"error": "unsupported session version"}, status_code=400)
    root = session.get("root")
    if root is not None and not isinstance(root, str):
        return None, JSONResponse({"error": "session.root must be a string"}, status_code=400)
    for key in ("config", "scan", "selection", "modes", "outputs", "provider_policy"):
        if key in session and session.get(key) is not None and not isinstance(session.get(key), dict):
            return None, JSONResponse({"error": f"session.{key} must be an object"}, status_code=400)
    session.setdefault("version", 1)
    session.setdefault("created_at", _utc_now_iso())
    session.setdefault("config", {})
    session.setdefault("scan", {})
    session.setdefault("selection", {})
    session.setdefault("modes", {})
    session.setdefault("outputs", {})
    session.setdefault("provider_policy", {})
    session["diagnostics"] = _workspace_session_diagnostics(session)
    session["id"] = str(session.get("id") or sha256(json.dumps(session, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16])
    return session, None


def _workspace_session_config_patch(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    allowed = set(_config_snapshot().keys())
    return {key: copy.deepcopy(value) for key, value in config.items() if key in allowed}


@app.get("/api/session/current", response_model=WorkspaceSessionEnvelope)
def get_session_current() -> JSONResponse:
    return JSONResponse({"session": _current_workspace_session()})


@app.post("/api/session/export", response_model=WorkspaceSessionEnvelope)
def post_session_export(payload: Dict[str, Any] = Body(None)) -> JSONResponse:
    payload = payload if isinstance(payload, dict) else {}
    return JSONResponse({"session": _current_workspace_session(payload)})


@app.post("/api/session/import", response_model=WorkspaceSessionMutationResponse)
def post_session_import(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    session, err = _normalise_workspace_session(payload)
    if err is not None or session is None:
        return err or JSONResponse({"error": "invalid session"}, status_code=400)
    diagnostics = session.get("diagnostics", {})
    status = 409 if diagnostics.get("missing_root") or diagnostics.get("out_of_root_paths") else 200
    return JSONResponse({"ok": status == 200, "session": session, "diagnostics": diagnostics}, status_code=status)


@app.post("/api/session/select", response_model=WorkspaceSessionMutationResponse)
def post_session_select(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    session, err = _normalise_workspace_session(payload)
    if err is not None or session is None:
        return err or JSONResponse({"error": "invalid session"}, status_code=400)
    diagnostics = session.get("diagnostics", {})
    if diagnostics.get("missing_root") or diagnostics.get("out_of_root_paths"):
        return JSONResponse({"ok": False, "session": session, "diagnostics": diagnostics}, status_code=409)
    ctx = _require_ctx()
    config_patch = _workspace_session_config_patch(session.get("config"))
    root = session.get("root")
    if isinstance(root, str) and root:
        config_patch["last_directory"] = root
        _ensure_config_for_path(root, ctx=ctx)
    if config_patch:
        with ctx.config_lock:
            ctx.config.update_config(config_patch)
    modes = session.get("modes") if isinstance(session.get("modes"), dict) else {}
    per_file_modes = modes.get("per_file_modes") if isinstance(modes.get("per_file_modes"), dict) else {}
    per_folder_modes = modes.get("per_folder_modes") if isinstance(modes.get("per_folder_modes"), dict) else {}
    if per_file_modes:
        cfg = ctx.config.get_config()
        cfg["per_file_modes"] = per_file_modes
        ctx.core.configure(cfg)
        try:
            ctx.config.update_config({"per_file_modes": per_file_modes})
        except Exception:
            pass
    if per_folder_modes:
        cfg = ctx.config.get_config()
        cfg["per_folder_modes"] = per_folder_modes
        ctx.core.configure(cfg)
        try:
            ctx.config.update_config({"per_folder_modes": per_folder_modes})
        except Exception:
            pass
    return JSONResponse({"ok": True, "session": _current_workspace_session(), "diagnostics": diagnostics})


@app.post("/api/provider/preflight", response_model=ProviderPreflightResponse)
def post_provider_preflight(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    srv = _require_ctx()
    action = str(payload.get("action") or "").strip().lower()
    cfg = srv.config.get_config()

    if action == "summarize":
        path = payload.get("path") or cfg.get("last_directory")
        if not isinstance(path, str) or not path:
            return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
        base = Path(path)
        files_abs, err = _resolve_input_file_values(payload.get("selected_paths"), base, srv, field_name="selected_paths")
        if err is not None or not files_abs:
            return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
        content_mode = _normalize_content_mode(payload.get("content_mode", "code_plus_short"), default="code_plus_short")
        return JSONResponse(
            _summary_provider_preflight(
                srv,
                "summarize",
                cfg=cfg,
                root=str(base),
                target=str(base),
                paths=files_abs,
                content_mode=content_mode,
            )
        )

    if action in {"summarize_folder", "summarize_repo"}:
        root = cfg.get("last_directory")
        if not isinstance(root, str) or not root:
            return JSONResponse({"error": "root path is missing; run scan first"}, status_code=400)
        scan_ctx = srv.core.scan_ctx
        scan_paths = [str(meta.abs_path) for meta in getattr(scan_ctx, "files", [])] if scan_ctx is not None else []
        target = root
        if action == "summarize_folder":
            target = payload.get("path") if isinstance(payload.get("path"), str) else root
        mode = _summary_mode_or_default(payload.get("mode") if isinstance(payload.get("mode"), str) else None, default="full")
        return JSONResponse(
            _summary_provider_preflight(
                srv,
                action,
                cfg=cfg,
                root=root,
                target=str(target),
                paths=scan_paths,
                mode=mode,
            )
        )

    if action == "watch_tick":
        path = payload.get("path") or cfg.get("last_directory")
        if not isinstance(path, str) or not path:
            return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
        base = Path(path)
        files_abs, err = _resolve_input_file_values(payload.get("selected_paths"), base, srv, field_name="selected_paths")
        if err is not None or not files_abs:
            return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
        return JSONResponse(
            _summary_provider_preflight(
                srv,
                "watch_tick",
                cfg=cfg,
                root=str(base),
                target=str(base),
                paths=files_abs,
                mode="summary",
            )
        )

    if action == "prune":
        path = payload.get("path") or cfg.get("last_directory")
        if not isinstance(path, str) or not path:
            return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
        run_cfg = _build_prune_run_config(cfg, payload)
        selected = payload.get("selected_paths") if isinstance(payload.get("selected_paths"), list) else None
        visible = payload.get("visible_paths") if isinstance(payload.get("visible_paths"), list) else None
        paths = selected or visible or []
        risks = _prune_provider_risks(run_cfg)
        return JSONResponse(
            _provider_preflight_decision(
                srv,
                "prune",
                cfg=run_cfg,
                root=path,
                target=path,
                paths=[str(item) for item in paths if isinstance(item, str)],
                requires_confirmation=bool(risks),
                risks=risks,
                estimated_provider_calls=max(1, len(risks)) if risks else 0,
            )
        )

    if action == "pair_ai":
        src_a = payload.get("src_a")
        src_b = payload.get("src_b")
        if not all(isinstance(x, str) and x for x in (src_a, src_b)):
            return JSONResponse({"error": "src_a and src_b are required"}, status_code=400)
        return JSONResponse(
            _provider_preflight_decision(
                srv,
                "pair_ai",
                cfg=cfg,
                root=str(cfg.get("last_directory") or ""),
                target=f"{src_a} :: {src_b}",
                paths=[],
                requires_confirmation=True,
                risks=["Pair+AI compares matching files through the AI summarizer."],
                estimated_provider_calls=1,
            )
        )

    if action == "merge":
        path = payload.get("path") or cfg.get("last_directory")
        if not isinstance(path, str) or not path:
            return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
        base = Path(path)
        files_abs, err = _resolve_selected_paths(payload, base, srv)
        if err is not None or not files_abs:
            return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
        return JSONResponse(_merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload))

    return JSONResponse(
        {"error": "action must be one of: summarize, summarize_folder, summarize_repo, watch_tick, prune, pair_ai, merge"},
        status_code=400,
    )


@app.get("/api/config", response_model=ConfigEnvelope)
def get_config() -> JSONResponse:
    ctx = _require_ctx()
    _ensure_config_for_path(ctx.config.get_config().get("last_directory"), ctx=ctx)
    return JSONResponse({"config": _config_snapshot()})


@app.put("/api/config", response_model=ConfigEnvelope)
def put_config(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    # Accept partial updates on a whitelisted set of keys
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)

    allowed_top = {
        "last_directory": str,
        "last_output": str,
        "selected_preset": str,
        "view_mode": str,
        "file_types": dict,
        "category_mask": dict,
        "last_filters": dict,
        "sort_by": str,
        "sort_descending": bool,
        "show_totals_columns": bool,
        "show_density_column": bool,
        "show_bytes_footer": bool,
        # Filter panel / scan defaults
        "date_last_n_days": int,
        "always_include_docs": bool,
        "name_filter_glob": bool,
        "content_search": str,
        "content_regex": bool,
        "content_case_sensitive": bool,
        "content_max_kb": int,
        "duplicate_hash_max_kb": int,
        "content_hide_duplicates": bool,
        "content_mark_duplicates": bool,
        "apply_token_filters_to_transforms": bool,
        "ignore_binary_files": bool,
        "max_file_size_mb": (int, float),
        "follow_symlinks": bool,
        "max_walk_depth": int,
        "exclude_node_modules": bool,
        "exclude_ios_noise": bool,
        "exclude_python_noise": bool,
        "exclude_java_gradle_noise": bool,
        "exclude_web_build_noise": bool,
        "use_git_files": bool,
        "git_include_untracked": bool,
        "git_only_changed": bool,
        "git_since_ref": str,
        "git_until_ref": str,
        "exclude_vendored": bool,
        "watch_enabled": bool,
        "watch_interval_sec": int,
        "watch_sample_limit": int,
        # Prune defaults
        "prune_output_path": str,
        "prune_ranking_mode": str,
        "prune_engine": str,
        "prune_neighbors_mode": str,
        "prune_mode": str,
        "prune_token_budget": int,
        "prune_dry_run": bool,
        "prune_goal_llm_enabled": bool,
        "prune_goal_llm_span_select": bool,
        "prune_neighbor_max": int,
        "prune_exclude_tests": bool,
        "prune_downrank_noise": bool,
        "prune_noise_dirs": list,
        "prune_top_k": int,
        "prune_keep_neighbors": bool,
        "prune_max_repairs": int,
        "prune_span_to_file_threshold": int,
        "prune_keep_class_for_method": bool,
        "prune_core_budget_tokens": int,
        "prune_repair_budget_tokens": int,
        "prune_repair_max_spans_per_module": int,
        "prune_repair_prefer_spans": bool,
        "include_repo_summary_in_merge": bool,
        "include_repo_summary_mode": str,
    }

    patch: Dict[str, Any] = {}
    for key, expected in allowed_top.items():
        if key not in payload:
            continue
        val = payload[key]
        if val is None:
            continue
        if expected is str and not isinstance(val, str):
            return JSONResponse({"error": f"{key} must be a string"}, status_code=400)
        if expected is dict and not isinstance(val, dict):
            return JSONResponse({"error": f"{key} must be an object"}, status_code=400)
        if expected is bool and not isinstance(val, bool):
            return JSONResponse({"error": f"{key} must be a boolean"}, status_code=400)
        if expected is int and (not isinstance(val, int) or isinstance(val, bool)):
            return JSONResponse({"error": f"{key} must be an integer"}, status_code=400)
        if expected is list and not isinstance(val, list):
            return JSONResponse({"error": f"{key} must be an array"}, status_code=400)
        if isinstance(expected, tuple):
            if not isinstance(val, expected) or isinstance(val, bool):
                return JSONResponse({"error": f"{key} must be a number"}, status_code=400)
        if key == "file_types":
            # normalize to str->bool and merge into existing map, not replace
            ft_patch: Dict[str, bool] = {}
            for k, v in (val or {}).items():
                if not isinstance(k, str) or not isinstance(v, bool):
                    return JSONResponse({"error": "file_types must be {str: bool}"}, status_code=400)
                ft_patch[k] = v
            if ft_patch:
                patch["file_types"] = ft_patch
        elif key == "category_mask":
            # merge mask rather than replace wholesale
            cm_patch: Dict[str, bool] = {}
            for k, v in (val or {}).items():
                if not isinstance(k, str) or not isinstance(v, bool):
                    return JSONResponse({"error": "category_mask must be {str: bool}"}, status_code=400)
                cm_patch[k] = v
            if cm_patch:
                patch["category_mask"] = cm_patch
        elif key == "last_filters":
            lf_patch: Dict[str, Any] = {}
            for subk, subv in (val or {}).items():
                if subv is None:
                    continue
                # basic numeric/string validation; UI MVP uses these keys
                if subk in {"token_min", "token_max"}:
                    if not isinstance(subv, int) or isinstance(subv, bool):
                        return JSONResponse({"error": f"last_filters.{subk} must be int"}, status_code=400)
                elif subk in {"size_min", "size_max"}:
                    if not isinstance(subv, (int, float)) or isinstance(subv, bool):
                        return JSONResponse({"error": f"last_filters.{subk} must be number"}, status_code=400)
                elif subk in {"name_pattern"}:
                    if not isinstance(subv, str):
                        return JSONResponse({"error": f"last_filters.{subk} must be string"}, status_code=400)
                elif subk in {"date_after", "date_before"}:
                    if isinstance(subv, str):
                        subv = _parse_date_to_ts(subv)
                    elif not isinstance(subv, (int, float)) or isinstance(subv, bool):
                        return JSONResponse({"error": f"last_filters.{subk} must be number (epoch) or date string"}, status_code=400)
                lf_patch[subk] = subv
            if lf_patch:
                patch["last_filters"] = lf_patch
        elif key == "include_repo_summary_mode":
            mode = str(val or "").strip().lower()
            if mode not in {"quick", "full"}:
                return JSONResponse({"error": "include_repo_summary_mode must be 'quick' or 'full'"}, status_code=400)
            patch[key] = mode
        elif key == "prune_noise_dirs":
            patch["prune_noise_dirs"] = [str(x) for x in val if isinstance(x, str) and x]
        else:
            patch[key] = val

    updated = len(patch)
    if patch:
        ctx = _require_ctx()
        if "last_directory" in patch:
            _ensure_config_for_path(patch.get("last_directory"), ctx=ctx)
        with ctx.config_lock:
            ctx.config.update_config(patch)

    return JSONResponse({"config": _config_snapshot(), "updated": updated})


@app.post("/api/allow_path", response_model=PathAllowResponse)
def post_allow_path(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    raw = payload.get("path")
    if not isinstance(raw, str) or not raw.strip():
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    raw = raw.strip()
    ctx = _require_ctx()
    base = Path(ctx.config.get_config().get("last_directory", "") or ".")
    p = Path(raw)
    if not p.is_absolute():
        p = base / p
    try:
        p = p.resolve()
    except Exception:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    purpose = str(payload.get("purpose") or "output_path_hint").strip().lower()
    granted = False
    if purpose == "generated_output" and p.exists():
        policy = _path_policy_for(base, ctx=ctx)
        if policy.is_generated_output(p):
            _allow_path(p)
            granted = True
        else:
            return JSONResponse({"error": "generated_output grant requires an existing generated output"}, status_code=403)
    elif purpose not in {"output_path_hint", "manual_selection"}:
        return JSONResponse({"error": "unsupported path purpose"}, status_code=400)
    return JSONResponse({"ok": True, "path": str(p), "granted": granted, "purpose": purpose})


@app.post("/api/path/exists", response_model=PathExistsResponse)
def post_path_exists(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    raw = payload.get("path")
    if not isinstance(raw, str) or not raw.strip():
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    raw = raw.strip()
    ctx = _require_ctx()
    base = Path(ctx.config.get_config().get("last_directory", "") or ".")
    p = Path(raw)
    if not p.is_absolute():
        p = base / p
    try:
        p = p.resolve()
    except Exception:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    policy = _path_policy_for(base, ctx=ctx)
    if not policy.can_stat(p):
        return JSONResponse({"error": "access denied"}, status_code=403)
    return JSONResponse({"exists": p.exists(), "is_file": p.is_file(), "is_dir": p.is_dir()})


# ----------------- User presets -----------------
@app.get("/api/presets", response_model=PresetNamesResponse)
def get_presets() -> JSONResponse:
    cfg = _require_ctx().config.get_config()
    store = cfg.get("user_presets", {})
    if not isinstance(store, dict):
        store = {}
    names = sorted(str(k) for k in store.keys() if isinstance(k, str))
    return JSONResponse({"names": names})


@app.get("/api/presets/{name}")
def get_preset(name: str) -> JSONResponse:
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name must be a non-empty string"}, status_code=400)
    name = name.strip()
    cfg = _require_ctx().config.get_config()
    store = cfg.get("user_presets", {})
    store_dict: Dict[str, Any] = store if isinstance(store, dict) else {}
    preset = store_dict.get(name)
    if not isinstance(preset, dict):
        return JSONResponse({"error": f"preset not found: {name}"}, status_code=404)
    return JSONResponse({"name": name, "preset": preset})


@app.post("/api/presets")
def post_presets(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name must be a non-empty string"}, status_code=400)
    name = name.strip()

    preset = payload.get("preset")
    if preset is None:
        preset = payload.get("config_snapshot")
    if not isinstance(preset, dict):
        return JSONResponse({"error": "preset must be an object"}, status_code=400)

    ctx = _require_ctx()
    with ctx.config_lock:
        cfg = ctx.config.get_config()
        store = cfg.get("user_presets", {})
        store_dict: Dict[str, Any] = store if isinstance(store, dict) else {}
        updated = dict(store_dict)
        updated[name] = preset
        cfg["user_presets"] = updated
        cfg["selected_preset"] = name
        cfg["last_preset"] = name
        try:
            ctx.config.save_config()
        except Exception:
            ctx.config.update_config({"user_presets": updated, "selected_preset": name, "last_preset": name})

    names = sorted(str(k) for k in updated.keys() if isinstance(k, str))
    return JSONResponse({"ok": True, "names": names})


@app.delete("/api/presets/{name}")
def delete_preset(name: str) -> JSONResponse:
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name must be a non-empty string"}, status_code=400)
    name = name.strip()

    ctx = _require_ctx()
    with ctx.config_lock:
        cfg = ctx.config.get_config()
        store = cfg.get("user_presets", {})
        store_dict: Dict[str, Any] = store if isinstance(store, dict) else {}
        updated = dict(store_dict)
        existed = name in updated
        updated.pop(name, None)
        cfg["user_presets"] = updated
        if cfg.get("selected_preset") == name:
            cfg["selected_preset"] = ""
        try:
            ctx.config.save_config()
        except Exception:
            ctx.config.update_config({"user_presets": updated, "selected_preset": cfg.get("selected_preset", "")})

    names = sorted(str(k) for k in updated.keys() if isinstance(k, str))
    return JSONResponse({"ok": True, "deleted": existed, "names": names})


def _apply_preset_by_name(name: str) -> Dict[str, Any]:
    if not name or not isinstance(name, str):
        raise KeyError(name)
    name = name.strip()
    if not name:
        raise KeyError(name)

    ctx = _require_ctx()
    with ctx.config_lock:
        cfg = ctx.config.get_config()
        store = cfg.get("user_presets", {})
        store_dict: Dict[str, Any] = store if isinstance(store, dict) else {}
        preset = store_dict.get(name)
        if not isinstance(preset, dict):
            raise KeyError(name)

        normalized = presets_service.apply_preset(preset, cfg)

        cat_mask = normalized.get("category_mask", {})
        categories = [
            k
            for k, v in (cat_mask.items() if isinstance(cat_mask, dict) else [])
            if isinstance(k, str) and bool(v)
        ]

        lf_patch = dict(cfg.get("last_filters", {})) if isinstance(cfg.get("last_filters"), dict) else {}
        filters_raw = normalized.get("filters", {})
        if isinstance(filters_raw, dict):
            lf_patch.update(
                {
                    "categories": categories,
                    "token_min": int(filters_raw.get("token_min", lf_patch.get("token_min", 0)) or 0),
                    "token_max": int(filters_raw.get("token_max", lf_patch.get("token_max", DEFAULT_TOKEN_MAX)) or 0),
                    "size_min": float(filters_raw.get("size_min", lf_patch.get("size_min", 0.0)) or 0.0),
                    "size_max": float(filters_raw.get("size_max", lf_patch.get("size_max", DEFAULT_SIZE_MAX)) or 0.0),
                    "name_pattern": str(filters_raw.get("name_pattern", lf_patch.get("name_pattern", "")) or ""),
                    "date_after": float(filters_raw.get("date_after", lf_patch.get("date_after", 0.0)) or 0.0),
                    "date_before": float(filters_raw.get("date_before", lf_patch.get("date_before", 0.0)) or 0.0),
                    "date_after_manual": bool(filters_raw.get("date_after_manual", lf_patch.get("date_after_manual", False))),
                    "date_before_manual": bool(filters_raw.get("date_before_manual", lf_patch.get("date_before_manual", False))),
                }
            )

        patch: Dict[str, Any] = {
            "file_types": normalized.get("file_types", {}),
            "category_mask": cat_mask,
            "last_filters": lf_patch,
            "view_mode": normalized.get("view_mode", cfg.get("view_mode", "hierarchy")),
            "ignore_binary_files": bool(normalized.get("ignore_binary_files", cfg.get("ignore_binary_files", False))),
            "max_file_size_mb": float(normalized.get("max_file_size_mb", cfg.get("max_file_size_mb", 0.0)) or 0.0),
            "follow_symlinks": bool(normalized.get("follow_symlinks", cfg.get("follow_symlinks", False))),
            "exclude_node_modules": bool(normalized.get("exclude_node_modules", cfg.get("exclude_node_modules", True))),
            "exclude_python_noise": bool(normalized.get("exclude_python_noise", cfg.get("exclude_python_noise", False))),
            "exclude_java_gradle_noise": bool(normalized.get("exclude_java_gradle_noise", cfg.get("exclude_java_gradle_noise", False))),
            "exclude_web_build_noise": bool(normalized.get("exclude_web_build_noise", cfg.get("exclude_web_build_noise", False))),
            "always_include_docs": bool(normalized.get("always_include_docs", cfg.get("always_include_docs", True))),
            "name_filter_glob": bool(normalized.get("name_filter_glob", cfg.get("name_filter_glob", False))),
            "show_totals_columns": bool(normalized.get("show_totals_columns", cfg.get("show_totals_columns", False))),
            "show_density_column": bool(normalized.get("show_density_column", cfg.get("show_density_column", False))),
            "show_bytes_footer": bool(normalized.get("show_bytes_footer", cfg.get("show_bytes_footer", True))),
        }
        if "exclude_ios_noise" in normalized:
            patch["exclude_ios_noise"] = bool(normalized.get("exclude_ios_noise", cfg.get("exclude_ios_noise", True)))
        if "sort_by" in normalized:
            patch["sort_by"] = str(normalized.get("sort_by") or cfg.get("sort_by", "name"))
        if "sort_descending" in normalized:
            patch["sort_descending"] = bool(normalized.get("sort_descending", cfg.get("sort_descending", False)))

        ctx.config.update_config(patch)
        ctx.config.update_config({"last_preset": name, "selected_preset": name})

        return _config_snapshot()


@app.post("/api/presets/load")
def post_presets_load(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name must be a non-empty string"}, status_code=400)
    try:
        snap = _apply_preset_by_name(name)
    except KeyError:
        return JSONResponse({"error": f"preset not found: {name}"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "config": snap})


@app.post("/api/presets/{name}/load")
def post_presets_load_path(name: str) -> JSONResponse:
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name must be a non-empty string"}, status_code=400)
    try:
        snap = _apply_preset_by_name(name)
    except KeyError:
        return JSONResponse({"error": f"preset not found: {name}"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "config": snap})


# ----------------- Manual file includes -----------------
def _normalize_manual_paths(
    paths: object,
    *,
    cfg: Dict[str, Any],
    processor: FileProcessor,
    recurse: bool = True,
    max_depth: int = 0,
    respect_excludes: bool = True,
    ignore_binary: bool = False,
) -> tuple[set[str], list[str]]:
    if not isinstance(paths, list):
        return set(), ["paths must be an array of strings"]

    out: set[str] = set()
    errors: list[str] = []
    max_depth = max(0, int(max_depth or 0))

    excluded_dirs: set[str] = set()
    excluded_files: set[str] = set()
    excluded_dirs_rel: set[str] = set()
    excluded_dirs_abs: set[str] = set()
    excluded_files_name: set[str] = set()
    excluded_files_rel: set[str] = set()
    excluded_files_abs: set[str] = set()
    root_abs = ""
    if respect_excludes:
        excluded_dirs = _compute_excluded_dirs(cfg, None, processor=processor)
        excluded_files = _effective_excluded_files(cfg)
        root_raw = cfg.get("last_directory", "")
        if isinstance(root_raw, str) and root_raw:
            try:
                root_abs = os.path.abspath(root_raw)
            except Exception:
                root_abs = root_raw
        excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(excluded_dirs)
        excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(excluded_files)

    def _rel_norm(path: str) -> str:
        if not root_abs:
            return ""
        try:
            return norm_path(os.path.relpath(path, root_abs))
        except Exception:
            return ""

    def _skip_file(path: str) -> bool:
        if respect_excludes:
            name_norm = norm_path(os.path.basename(path))
            if name_norm in excluded_files_name:
                errors.append(f"Excluded file skipped: {path}")
                return True
            abs_norm = norm_path(path)
            if abs_norm in excluded_files_abs:
                errors.append(f"Excluded file skipped: {path}")
                return True
            rel_norm = _rel_norm(path)
            if rel_norm and rel_norm in excluded_files_rel:
                errors.append(f"Excluded file skipped: {path}")
                return True
        if ignore_binary:
            try:
                if processor.is_binary_file(path):
                    errors.append(f"Binary file skipped: {path}")
                    return True
            except Exception:
                pass
        return False

    def _iter_dir_files(root: str) -> None:
        if not recurse:
            try:
                for entry in os.scandir(root):
                    if not entry.is_file():
                        continue
                    fp = os.path.normpath(os.path.abspath(entry.path))
                    if not _skip_file(fp):
                        out.add(fp)
            except Exception:
                errors.append(f"Failed to read folder: {root}")
            return

        base_depth = len(Path(root).resolve().parts)
        for cur_root, dirnames, filenames in os.walk(root):
            try:
                cur_depth = len(Path(cur_root).resolve().parts) - base_depth
            except Exception:
                cur_depth = 0
            if max_depth > 0 and cur_depth >= max_depth:
                dirnames[:] = []
            if respect_excludes:
                keep_dirs: list[str] = []
                for d in dirnames:
                    abs_dir = os.path.join(cur_root, d)
                    name_norm = norm_path(d)
                    abs_norm = norm_path(abs_dir)
                    rel_norm = _rel_norm(abs_dir)
                    if (
                        name_norm in excluded_dirs_rel
                        or (rel_norm and rel_norm in excluded_dirs_rel)
                        or (excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs))
                    ):
                        continue
                    try:
                        if processor.is_virtual_env(abs_dir):
                            continue
                    except Exception:
                        pass
                    keep_dirs.append(d)
                dirnames[:] = keep_dirs
            for name in filenames:
                fp = os.path.normpath(os.path.abspath(os.path.join(cur_root, name)))
                if os.path.isfile(fp) and not _skip_file(fp):
                    out.add(fp)

    for item in paths:
        if not isinstance(item, str):
            continue
        raw = item.strip()
        if not raw:
            continue
        try:
            expanded = os.path.expanduser(raw)
            expanded = os.path.expandvars(expanded)
        except Exception:
            expanded = raw
        try:
            norm = os.path.normpath(os.path.abspath(expanded))
        except Exception:
            errors.append(f"Invalid path: {raw}")
            continue

        if os.path.isfile(norm):
            if not _skip_file(norm):
                out.add(norm)
            continue
        if os.path.isdir(norm):
            if respect_excludes:
                base_name = norm_path(os.path.basename(norm))
                abs_norm = norm_path(norm)
                rel_norm = _rel_norm(norm)
                if (
                    base_name in excluded_dirs_rel
                    or (rel_norm and rel_norm in excluded_dirs_rel)
                    or (excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs))
                ):
                    errors.append(f"Excluded folder skipped: {norm}")
                    continue
            _iter_dir_files(norm)
            continue

        errors.append(f"Not found: {norm}")

    return out, errors


@app.post("/api/files/manual")
def post_files_manual(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    ctx = _require_ctx()
    _ensure_config_for_path(ctx.config.get_config().get("last_directory"), ctx=ctx)
    normalized, errors = _normalize_manual_paths(
        payload.get("paths"),
        cfg=ctx.config.get_config(),
        processor=ctx.processor,
        recurse=bool(payload.get("recurse", True)),
        max_depth=int(payload.get("max_depth", 0) or 0),
        respect_excludes=bool(payload.get("respect_excludes", True)),
        ignore_binary=bool(payload.get("ignore_binary", False)),
    )
    if not normalized and errors:
        return JSONResponse({"error": "no valid paths", "errors": errors}, status_code=400)

    added = ctx.core.add_manual_includes(normalized)
    total = len(ctx.core.manual_includes_snapshot())
    removed: list[str] = []
    try:
        cfg = ctx.config.get_config()
        custom = cfg.get("custom_excluded", [])
        if isinstance(custom, list) and custom:
            root = cfg.get("last_directory", "")
            remove_set: set[str] = set()
            for p in normalized:
                if not isinstance(p, str):
                    continue
                remove_set.add(p)
                if isinstance(root, str) and root and p.startswith(root):
                    rel = p[len(root):].lstrip("/\\")
                    if rel:
                        remove_set.add(rel)
                        remove_set.add(rel.lstrip("./"))
            if remove_set:
                new_list = [x for x in custom if x not in remove_set]
                if len(new_list) != len(custom):
                    removed = [x for x in custom if x in remove_set]
                    ctx.config.update_config({"custom_excluded": new_list})
    except Exception:
        removed = []
    return JSONResponse({"ok": True, "added": added, "total_manual": total, "errors": errors, "removed_excluded": removed})


@app.delete("/api/files/manual")
def delete_files_manual() -> JSONResponse:
    ctx = _require_ctx()
    ctx.core.clear_manual_includes()
    return JSONResponse({"ok": True})


# ----------------- Filesystem listing (manual picker) -----------------
def _fs_match(pattern: str, name: str, full_path: str) -> bool:
    if not pattern:
        return True
    raw = pattern.strip()
    if not raw:
        return True
    if any(ch in raw for ch in "*?[]"):
        return fnmatch.fnmatchcase(name, raw) or fnmatch.fnmatchcase(full_path, raw)
    lowered = raw.lower()
    return lowered in name.lower() or lowered in full_path.lower()


@app.post("/api/fs/list")
def post_fs_list(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    ctx = _require_ctx()
    cfg = ctx.config.get_config()
    path_raw = payload.get("path") or cfg.get("last_directory") or os.path.expanduser("~")
    if not isinstance(path_raw, str) or not path_raw:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    show_hidden = _safe_bool(payload.get("show_hidden", False))
    pattern = payload.get("pattern") if isinstance(payload.get("pattern"), str) else ""
    only_dirs = _safe_bool(payload.get("dirs_only", False))
    only_files = _safe_bool(payload.get("files_only", False))
    limit = int(payload.get("limit", 2000) or 2000)
    root_raw = payload.get("root") or cfg.get("last_directory") or ""
    restrict_root = _safe_bool(payload.get("restrict_root", True if root_raw else False), default=True)
    allow_external_browse = _safe_bool(payload.get("allow_external_browse", False))

    try:
        expanded = os.path.expandvars(os.path.expanduser(str(path_raw)))
    except Exception:
        expanded = str(path_raw)
    base = Path(cfg.get("last_directory") or ".")
    p = Path(expanded)
    if not p.is_absolute():
        p = base / p
    try:
        p = p.resolve()
    except Exception:
        pass
    if not p.exists() or not p.is_dir():
        return JSONResponse({"error": f"path is not a directory: {p}"}, status_code=400)

    if restrict_root and isinstance(root_raw, str) and root_raw:
        root = Path(os.path.expandvars(os.path.expanduser(root_raw)))
        if not root.is_absolute():
            root = base / root
        try:
            root = root.resolve()
            _ = p.relative_to(root)
        except Exception:
            return JSONResponse({"error": "access denied"}, status_code=403)
    elif not restrict_root:
        try:
            policy = _path_policy_for(root_raw or base, ctx=ctx, allow_external_browse=allow_external_browse)
            if not policy.can_list(p, restrict_root=False):
                return JSONResponse({"error": "access denied"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "access denied"}, status_code=403)

    entries: list[dict[str, Any]] = []
    try:
        with os.scandir(p) as it:
            for entry in it:
                name = entry.name
                if not show_hidden and name.startswith("."):
                    continue
                full_path = entry.path
                if pattern and not _fs_match(pattern, name, full_path):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except Exception:
                    is_dir = False
                if only_dirs and not is_dir:
                    continue
                if only_files and is_dir:
                    continue
                entries.append({"name": name, "path": full_path, "is_dir": is_dir})
                if limit and len(entries) >= limit:
                    break
    except PermissionError as exc:
        return JSONResponse({"error": f"Cannot access {p}: {exc}"}, status_code=403)
    except OSError as exc:
        return JSONResponse({"error": f"Cannot access {p}: {exc}"}, status_code=500)

    entries.sort(key=lambda item: (not item.get("is_dir", False), str(item.get("name", "")).lower()))
    parent = str(p.parent) if p.parent != p else str(p)
    return JSONResponse({"path": str(p), "parent": parent, "entries": entries})


# ----------------- Keep-only workflow -----------------
def _serialize_normalize_result(result: object) -> Dict[str, Any]:
    try:
        items_raw = getattr(result, "items", None) or []
        stats_raw = getattr(result, "stats", None) or {}
        requested_raw = getattr(result, "requested_rel", None) or set()
        cleaned_lines_raw = getattr(result, "cleaned_lines", None) or []
    except Exception:
        items_raw = []
        stats_raw = {}
        requested_raw = set()
        cleaned_lines_raw = []

    items: list[dict[str, Any]] = []
    for item in items_raw:
        try:
            raw = str(getattr(item, "raw", "") or "")
            cleaned = getattr(item, "cleaned", None)
            cleaned_str = "" if cleaned is None else str(cleaned)
            rel = getattr(item, "rel", None)
            rel_str = "" if rel is None else str(rel)
            status = str(getattr(item, "status", "") or "")
            note = getattr(item, "note", None)
            note_str = "" if note is None else str(note)
            resolved = rel_str or cleaned_str or ""
        except Exception:
            continue
        items.append(
            {
                "raw": raw,
                "cleaned": cleaned_str,
                "resolved": resolved,
                "status": status,
                "note": note_str,
            }
        )

    stats: dict[str, int] = {}
    if isinstance(stats_raw, dict):
        for k, v in stats_raw.items():
            if not isinstance(k, str):
                continue
            try:
                stats[k] = int(v)
            except Exception:
                continue

    for key in ("kept", "corrected_fuzzy", "corrected_llm", "ignored", "invalid", "missed"):
        stats.setdefault(key, 0)

    requested: list[str] = []
    try:
        if isinstance(requested_raw, (set, list, tuple)):
            requested = sorted(str(x) for x in requested_raw if isinstance(x, str) and x)
    except Exception:
        requested = []

    cleaned_lines: list[str] = []
    try:
        if isinstance(cleaned_lines_raw, list):
            cleaned_lines = [str(x) for x in cleaned_lines_raw if isinstance(x, str) and x]
    except Exception:
        cleaned_lines = []

    return {
        "items": items,
        "stats": stats,
        "requested_rel": requested,
        "cleaned_lines": cleaned_lines,
    }


@app.post("/api/path/normalize")
def post_path_normalize(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    text = payload.get("text")
    if not isinstance(text, str):
        return JSONResponse({"error": "text must be a string"}, status_code=400)
    options = payload.get("options") or {}
    if not isinstance(options, dict):
        options = {}

    scan_ctx = srv.core.scan_ctx
    if not scan_ctx or not scan_ctx.files:
        return JSONResponse({"error": "Scan a directory first."}, status_code=400)

    smart = bool(options.get("smart", True))
    autocorrect = bool(options.get("autocorrect", True))
    use_llm = bool(options.get("use_llm", False))
    dir_prefix = bool(options.get("dir_prefix", False))

    scanned_rel = {meta.rel_path.as_posix() for meta in scan_ctx.files}
    try:
        result = sanitize_pasted_paths(
            text,
            str(scan_ctx.root),
            scanned_rel,
            smart=smart,
            autocorrect=autocorrect,
            use_llm=use_llm,
            dir_prefix=dir_prefix,
        )
    except Exception as exc:
        return JSONResponse({"error": f"Failed to normalize paths: {exc}"}, status_code=500)

    return JSONResponse(_serialize_normalize_result(result))


@app.post("/api/keep_only/apply")
def post_keep_only_apply(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    keep_rels = payload.get("keep_rels")
    if not isinstance(keep_rels, list) or not all(isinstance(x, str) for x in keep_rels):
        return JSONResponse({"error": "keep_rels must be an array of strings"}, status_code=400)
    keep_rels = [x.strip() for x in keep_rels if isinstance(x, str) and x.strip()]
    if not keep_rels:
        return JSONResponse({"error": "keep_rels must not be empty"}, status_code=400)

    if not srv.core.scan_ctx or not srv.core.scan_ctx.files:
        return JSONResponse({"error": "Scan a directory first."}, status_code=400)

    kept, excluded, invalid = srv.core.apply_keep_only(keep_rels)

    with srv.config_lock:
        srv.config.update_config({"custom_excluded": sorted(srv.core.custom_excluded)})
        cfg = srv.config.get_config()

    srv.core.configure(cfg)
    return JSONResponse({"ok": True, "kept": kept, "excluded": excluded, "invalid": invalid})


@app.post("/api/files/from-parent")
def post_files_from_parent(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)

    scan_ctx = srv.core.scan_ctx
    if not scan_ctx or not scan_ctx.files:
        return JSONResponse({"paths": []})

    raw = payload.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raw = payload.get("file_path")
    if not isinstance(raw, str) or not raw.strip():
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)

    try:
        root = Path(os.path.abspath(str(scan_ctx.root)))
    except Exception:
        root = Path(str(scan_ctx.root))

    p = Path(raw.strip())
    if not p.is_absolute():
        p = root / p
    try:
        p = Path(os.path.abspath(str(p)))
    except Exception:
        pass

    try:
        parent = p if p.is_dir() else p.parent
    except Exception:
        parent = p.parent

    try:
        _ = parent.relative_to(root)
    except Exception:
        return JSONResponse({"error": "access denied", "paths": []}, status_code=403)

    out: list[str] = []
    for meta in scan_ctx.files:
        try:
            mp = Path(os.path.abspath(str(meta.abs_path)))
        except Exception:
            continue
        try:
            _ = mp.relative_to(parent)
        except Exception:
            continue
        out.append(str(mp))

    out = sorted(dict.fromkeys(out))
    return JSONResponse({"paths": out, "parent": str(parent)})


# ----------------- JSUI <-> Service DTO adapter -----------------
def _safe_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return default
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
        return default
    try:
        return bool(value)
    except Exception:
        return default


def _safe_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return default
            try:
                return int(s)
            except Exception:
                return int(float(s))
        return int(value)
    except Exception:
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return default
            return float(s)
        return float(value)
    except Exception:
        return default


def _parse_date_to_ts(value: object) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0

    from datetime import datetime as _dt, timezone as _tz

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
    ):
        try:
            return _dt.strptime(s, fmt).replace(tzinfo=_tz.utc).timestamp()
        except Exception:
            pass
    try:
        iso = (s[:-1] + "+00:00") if s.endswith("Z") else s
        dt = _dt.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        else:
            dt = dt.astimezone(_tz.utc)
        return dt.timestamp()
    except Exception:
        return 0.0

def _parse_date_to_ts_ex(value: object) -> tuple[float, bool]:
    if not value:
        return 0.0, True
    if isinstance(value, (int, float)):
        return float(value), True
    s = str(value).strip()
    if not s:
        return 0.0, True

    from datetime import datetime as _dt, timezone as _tz

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
    ):
        try:
            return _dt.strptime(s, fmt).replace(tzinfo=_tz.utc).timestamp(), True
        except Exception:
            pass
    try:
        iso = (s[:-1] + "+00:00") if s.endswith("Z") else s
        dt = _dt.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        else:
            dt = dt.astimezone(_tz.utc)
        return dt.timestamp(), True
    except Exception:
        return 0.0, False


def _category_mask_from_payload(filters: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[Category, bool]:
    selected = filters.get("categories")
    if isinstance(selected, list) and selected:
        enabled: set[Category] = set()
        for item in selected:
            try:
                enabled.add(Category[str(item).upper()])
            except Exception:
                continue
        if enabled:
            return {cat: (cat in enabled) for cat in Category}

    raw = cfg.get("category_mask", {})
    if not isinstance(raw, dict):
        return {cat: True for cat in Category}
    return {cat: bool(raw.get(cat.name, True)) for cat in Category}


def _payload_visibility_mode(payload: Dict[str, Any]) -> str:
    raw = payload.get("visibility_mode")
    if isinstance(raw, str):
        val = raw.strip().lower()
        if val in {"all", "everything", "show_all", "showall"}:
            return "all"
    if payload.get("show_all") is True:
        return "all"
    return "filters"


def _payload_show_all(payload: Dict[str, Any]) -> bool:
    return _payload_visibility_mode(payload) == "all"


_CONTENT_MODE_ALLOWED = {
    "code_only",
    "skeleton_only",
    "code_plus_skeleton",
    "code_plus_short_plus_skeleton",
    "code_plus_long_plus_skeleton",
    "code_plus_short",
    "code_plus_long",
    "short_only",
    "long_only",
    "short_plus_skeleton",
    "long_plus_skeleton",
}
_CONTENT_MODE_ALIASES = {
    "summary_short": "short_only",
    "summary_long": "long_only",
    "code": "code_only",
    "skeleton": "skeleton_only",
    "summary_short_plus_skeleton": "short_plus_skeleton",
    "summary_long_plus_skeleton": "long_plus_skeleton",
    "code_plus_summary_short_plus_skeleton": "code_plus_short_plus_skeleton",
    "code_plus_summary_long_plus_skeleton": "code_plus_long_plus_skeleton",
}


def _normalize_content_mode(
    raw: object,
    *,
    default: str = "code_only",
    warnings: list[str] | None = None,
    strict: bool = False,
) -> str:
    mode = str(raw or default).strip().lower()
    mode = _CONTENT_MODE_ALIASES.get(mode, mode)
    if mode not in _CONTENT_MODE_ALLOWED:
        if strict:
            raise ValueError(f"invalid content_mode: {mode!r}")
        if warnings is not None:
            warnings.append(f"Invalid content_mode ignored: {mode!r}")
        return default
    return mode


def _payload_to_ui_state(payload: Dict[str, Any], cfg: Dict[str, Any], *, warnings: list[str] | None = None) -> UiState:
    payload = payload if isinstance(payload, dict) else {}
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    show_all = _payload_show_all(payload)

    root_str = payload.get("path") if isinstance(payload.get("path"), str) else None
    if not root_str:
        root_str = cfg.get("last_directory") if isinstance(cfg.get("last_directory"), str) else ""
    root_dir = Path(root_str or ".")

    output_str = payload.get("output_file") if isinstance(payload.get("output_file"), str) else None
    if not output_str:
        output_str = cfg.get("last_output") if isinstance(cfg.get("last_output"), str) else ""
    output_file = Path(output_str) if output_str else (root_dir / "merged_files.txt")

    file_types_raw = cfg.get("file_types", {})
    file_types: Dict[str, bool] = {}
    if isinstance(file_types_raw, dict):
        for k, v in file_types_raw.items():
            if isinstance(k, str):
                file_types[k] = bool(v)

    scan = ScanOptions(
        include_all=True if show_all else _safe_bool(payload.get("include_all", True)),
        ignore_binary=False if show_all else _safe_bool(payload.get("ignore_binary", cfg.get("ignore_binary_files", False))),
        max_file_size_mb=0.0 if show_all else _safe_float(payload.get("max_file_size_mb", cfg.get("max_file_size_mb", 0.0))),
        sort_by=str(payload.get("sort_by", cfg.get("sort_by", "name")) or "name"),
        sort_desc=_safe_bool(payload.get("sort_descending", cfg.get("sort_descending", False))),
        max_depth=0 if show_all else _safe_int(payload.get("max_walk_depth", cfg.get("max_walk_depth", 0))),
        follow_symlinks=_safe_bool(payload.get("follow_symlinks", cfg.get("follow_symlinks", False))),
        use_git_files=False if show_all else _safe_bool(payload.get("use_git_files", False)),
        git_include_untracked=False if show_all else _safe_bool(payload.get("git_include_untracked", False)),
        token_workers=_safe_int(payload.get("token_workers", cfg.get("token_workers", 0)), 0),
        exclude_node_modules=False if show_all else _safe_bool(payload.get("exclude_node_modules", cfg.get("exclude_node_modules", True))),
        exclude_ios_noise=False if show_all else _safe_bool(payload.get("exclude_ios_noise", cfg.get("exclude_ios_noise", True))),
        exclude_python_noise=False if show_all else _safe_bool(payload.get("exclude_python_noise", cfg.get("exclude_python_noise", False))),
        exclude_java_gradle_noise=False if show_all else _safe_bool(payload.get("exclude_java_gradle_noise", cfg.get("exclude_java_gradle_noise", False))),
        exclude_web_build_noise=False if show_all else _safe_bool(payload.get("exclude_web_build_noise", cfg.get("exclude_web_build_noise", False))),
        git_since_ref="" if show_all else str(payload.get("git_since_ref", "") or ""),
        git_until_ref="HEAD" if show_all else str(payload.get("git_until_ref", "HEAD") or "HEAD") or "HEAD",
        git_only_changed=False if show_all else _safe_bool(payload.get("git_only_changed", False)),
        exclude_vendored=False if show_all else _safe_bool(payload.get("exclude_vendored", cfg.get("exclude_vendored", False))),
    )

    token_min = 0 if show_all else _safe_int(filters.get("token_min", cfg.get("token_filter_min", 0)), 0)
    token_max = DEFAULT_TOKEN_MAX if show_all else _safe_int(filters.get("token_max", cfg.get("token_filter_max", DEFAULT_TOKEN_MAX)), DEFAULT_TOKEN_MAX)
    size_min_kb = 0.0 if show_all else _safe_float(filters.get("size_min", cfg.get("size_filter_min_kb", 0.0)), 0.0)
    size_max_kb = DEFAULT_SIZE_MAX if show_all else _safe_float(filters.get("size_max", cfg.get("size_filter_max_kb", DEFAULT_SIZE_MAX)), DEFAULT_SIZE_MAX)

    xcode_target = payload.get("xcode_target")
    if not isinstance(xcode_target, str) or not xcode_target.strip():
        xcode_target = "(All)"

    always_include_docs = payload.get("always_include_docs")
    if always_include_docs is None:
        always_include_docs = filters.get("always_include_docs", cfg.get("always_include_docs", True))
    if show_all:
        always_include_docs = True

    always_list = cfg.get("always_include_docs_list", [])
    always_include_list = list(always_list) if isinstance(always_list, list) else []

    date_after_raw = 0 if show_all else (filters.get("date_after", 0) or 0)
    date_after_ts, date_after_ok = _parse_date_to_ts_ex(date_after_raw)
    if warnings is not None and not date_after_ok:
        warnings.append(f"Invalid Date after value ignored: {date_after_raw!r}")

    date_before_raw = 0 if show_all else (filters.get("date_before", 0) or 0)
    date_before_ts, date_before_ok = _parse_date_to_ts_ex(date_before_raw)
    if warnings is not None and not date_before_ok:
        warnings.append(f"Invalid Date before value ignored: {date_before_raw!r}")

    filter_opts = FilterOptions(
        category_mask={cat: True for cat in Category} if show_all else _category_mask_from_payload(filters, cfg),
        token_min=max(0, token_min),
        token_max=max(0, token_max),
        size_min_kb=max(0.0, size_min_kb),
        size_max_kb=max(0.0, size_max_kb),
        name_pattern="" if show_all else str(filters.get("name_pattern", cfg.get("name_pattern", "")) or ""),
        name_glob=False if show_all else _safe_bool(filters.get("name_filter_glob", cfg.get("name_filter_glob", False))),
        content_search="" if show_all else str(filters.get("content_search", cfg.get("content_search", "")) or ""),
        content_regex=False if show_all else _safe_bool(filters.get("content_regex", cfg.get("content_regex", False))),
        content_case=False if show_all else _safe_bool(filters.get("content_case_sensitive", cfg.get("content_case_sensitive", False))),
        content_max_kb=_safe_int(filters.get("content_max_kb", cfg.get("content_max_kb", 256)), 256),
        duplicate_hash_max_kb=_safe_int(
            filters.get("duplicate_hash_max_kb", cfg.get("duplicate_hash_max_kb", 256)),
            256,
        ),
        date_after_ts=date_after_ts,
        date_before_ts=date_before_ts,
        hide_duplicates=False if show_all else _safe_bool(filters.get("content_hide_duplicates", cfg.get("content_hide_duplicates", False))),
        mark_duplicates=_safe_bool(filters.get("content_mark_duplicates", cfg.get("content_mark_duplicates", True))),
        apply_token_filters_to_transforms=False if show_all else _safe_bool(
            filters.get(
                "apply_token_filters_to_transforms",
                cfg.get("apply_token_filters_to_transforms", False),
            )
        ),
        enumerate_lines=_safe_bool(payload.get("enumerate_lines", cfg.get("enumerate_lines", False))),
        remove_empty=_safe_bool(payload.get("remove_empty", cfg.get("remove_empty", False))),
        xcode_target="(All)" if show_all else str(xcode_target),
        always_include_docs=_safe_bool(always_include_docs, True),
        always_include_list=always_include_list,
    )

    output = OutputOptions(
        include_tree=_safe_bool(payload.get("include_file_tree", cfg.get("include_file_tree", True))),
        json_manifest=_safe_bool(payload.get("create_json_manifest", cfg.get("include_json_manifest", False))),
        custom_start=str(cfg.get("custom_start_label", "======= START OF FILE: {file_display} =======")),
        custom_end=str(cfg.get("custom_end_label", "======= END OF FILE: {file_display} =======")),
        tree_start=str(cfg.get("custom_tree_start_label", "======= FILE TREE =======")),
        tree_end=str(cfg.get("custom_tree_end_label", "======= END OF FILE TREE =======")),
        markdown_fences=_safe_bool(payload.get("markdown_fences", cfg.get("output_markdown_fences", False))),
        markdown_toc=_safe_bool(payload.get("markdown_toc", cfg.get("output_markdown_toc", False))),
        footer_final_only=_safe_bool(cfg.get("footer_final_only", False)),
    )

    merge_mode = str(payload.get("structure_mode", cfg.get("structure_mode", "single_file")) or "single_file").strip()
    if merge_mode not in {"single_file", "split_lines", "split_tokens"}:
        if warnings is not None:
            warnings.append(f"Invalid structure_mode ignored: {merge_mode!r}")
        merge_mode = "single_file"

    split_default = _safe_int(cfg.get("split_into_n", 5), 5)
    split_into_n = _safe_int(payload.get("split_into_n", split_default), split_default)
    split_into_n = max(1, split_into_n)

    token_default = _safe_int(cfg.get("token_limit", 1_048_576), 1_048_576)
    token_limit = _safe_int(payload.get("token_limit", token_default), token_default)
    token_limit = max(1000, token_limit)

    merge = MergeStructure(mode=merge_mode, split_into_n=split_into_n, token_limit=token_limit)

    content_mode = _normalize_content_mode(
        payload.get("content_mode", cfg.get("content_mode", "code_only")),
        default="code_only",
        warnings=warnings,
    )

    summary = SummaryOptions(
        content_mode=content_mode,
        prompt_template=str(payload.get("prompt_template", cfg.get("prompt_template", "")) or ""),
        groq_workers=_safe_int(cfg.get("groq_max_workers", 0), 0),
        use_batch=_safe_bool(cfg.get("groq_use_batch", False)),
        batch_progress=_safe_bool(cfg.get("batch_progress", False)),
    )

    return UiState(
        root_dir=root_dir,
        output_file=output_file,
        file_types=file_types,
        view_mode=str(cfg.get("view_mode", "hierarchy") or "hierarchy"),
        copy_to_clipboard=_safe_bool(cfg.get("copy_to_clipboard", False)),
        scan=scan,
        filters=filter_opts,
        output=output,
        merge=merge,
        summary=summary,
    )

def _norm_str_set(items: object) -> set[str]:
    if not isinstance(items, (list, set, tuple)):
        return set()
    out: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        norm = _normalize_exclusion_entry(item)
        if not norm:
            continue
        out.add(norm)
    return out

def _effective_excluded_files(cfg: Dict[str, Any]) -> set[str]:
    return {f for f in _norm_str_set(cfg.get("excluded_files", [])) if f}

def _content_mode_map_from_plan(plan: Dict[str, FileMode] | Dict[str, str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not isinstance(plan, dict):
        return mapping
    for raw_path, raw_mode in plan.items():
        if not isinstance(raw_path, str) or not raw_path:
            continue
        try:
            path = os.path.abspath(raw_path)
        except Exception:
            path = raw_path
        mode = None
        if isinstance(raw_mode, FileMode):
            mode = raw_mode
        else:
            try:
                key = str(raw_mode or "").strip().upper()
                if key in FileMode.__members__:
                    mode = FileMode[key]
            except Exception:
                mode = None
        if mode is FileMode.EXCLUDE:
            mapping[path] = "exclude"
        elif mode is FileMode.CODE:
            mapping[path] = "code"
        elif mode in (FileMode.SKELETON, FileMode.API_SURFACE, FileMode.SIGNATURES):
            mapping[path] = "skeleton"
        elif mode is FileMode.CODE_PLUS_SKELETON:
            mapping[path] = "code_plus_skeleton"
        elif mode is FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON:
            mapping[path] = "code_plus_summary_short_plus_skeleton"
        elif mode is FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON:
            mapping[path] = "code_plus_summary_long_plus_skeleton"
        elif mode is FileMode.SUMMARY_SHORT:
            mapping[path] = "summary_short"
        elif mode is FileMode.SUMMARY_LONG:
            mapping[path] = "summary_long"
        elif mode is FileMode.SUMMARY_SHORT_PLUS_SKELETON:
            mapping[path] = "summary_short_plus_skeleton"
        elif mode is FileMode.SUMMARY_LONG_PLUS_SKELETON:
            mapping[path] = "summary_long_plus_skeleton"
        elif mode is FileMode.CODE_PLUS_SHORT:
            mapping[path] = "code_plus_short"
        elif mode is FileMode.CODE_PLUS_LONG:
            mapping[path] = "code_plus_long"
    return mapping


def _is_under_root(path: str, root: str) -> bool:
    try:
        root_path = Path(root).resolve()
        target = Path(path).resolve()
        target.relative_to(root_path)
        return True
    except Exception:
        try:
            return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
        except Exception:
            return False


def _dedupe_folder_targets(paths: Iterable[str]) -> list[str]:
    cleaned = []
    for raw in sorted(set(paths), key=lambda p: len(p)):
        if not raw:
            continue
        normalized = os.path.abspath(raw)
        if any(normalized.startswith(p.rstrip(os.sep) + os.sep) for p in cleaned):
            continue
        cleaned.append(normalized)
    return cleaned


def _rel_path_for_tree(path: str, root: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except Exception:
        rel = path
    rel = rel.replace("\\", "/")
    return rel if rel else "."


def _list_children_from_scan(ctx: ScanContext, path_abs: str) -> list[dict]:
    root_abs = str(ctx.root)
    try:
        rel = os.path.relpath(path_abs, root_abs)
    except Exception:
        rel = ""
    rel = rel.replace("\\", "/")
    prefix = "" if rel in {"", ".", "./"} else rel.strip("/").rstrip("/") + "/"

    children: dict[str, dict] = {}
    for meta in ctx.files:
        rel_path = meta.rel_path.as_posix()
        if prefix and not rel_path.startswith(prefix):
            continue
        remainder = rel_path[len(prefix):] if prefix else rel_path
        if not remainder:
            continue
        if "/" in remainder:
            name = remainder.split("/", 1)[0]
            entry = children.setdefault(
                name,
                {
                    "kind": "folder",
                    "rel_path": (prefix + name).strip("/"),
                    "file_count": 0,
                    "token_count": 0,
                },
            )
            entry["file_count"] += 1
            entry["token_count"] += int(meta.token_count or 0)
        else:
            entry = children.setdefault(
                remainder,
                {
                    "kind": "file",
                    "rel_path": (prefix + remainder).strip("/"),
                    "size_bytes": int(meta.size_bytes or 0),
                    "token_count": int(meta.token_count or 0),
                    "ext": meta.ext or "",
                },
            )
            entry["size_bytes"] = int(meta.size_bytes or 0)
            entry["token_count"] = int(meta.token_count or 0)

    entries = list(children.values())
    entries.sort(key=lambda item: (0 if item["kind"] == "folder" else 1, item["rel_path"]))
    for entry in entries:
        entry["abs_path"] = str(Path(root_abs) / entry["rel_path"])
    return entries


def _ensure_required_summaries(
    *,
    plan: Dict[str, FileMode],
    processor: FileProcessor,
    cfg: Dict[str, Any],
    root_abs: str,
    strict_required: bool,
    manager: SummarizationManager | None = None,
) -> tuple[
    Dict[str, Optional[str]],
    Dict[str, Optional[str]],
    List[str],
]:
    short_required = [
        p
        for p, m in plan.items()
        if m in (
            FileMode.SUMMARY_SHORT,
            FileMode.CODE_PLUS_SHORT,
            FileMode.SUMMARY_SHORT_PLUS_SKELETON,
            FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
        )
    ]
    long_required = [
        p
        for p, m in plan.items()
        if m in (
            FileMode.SUMMARY_LONG,
            FileMode.CODE_PLUS_LONG,
            FileMode.SUMMARY_LONG_PLUS_SKELETON,
            FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
        )
    ]
    short_required = list(dict.fromkeys(short_required))
    long_required = list(dict.fromkeys(long_required))

    summaries_short: Dict[str, Optional[str]] = {}
    summaries_long: Dict[str, Optional[str]] = {}
    errors_all: List[str] = []

    def _fetch(kind: str, paths: list[str]) -> tuple[Dict[str, Optional[str]], List[str], List[str]]:
        if not paths:
            return {}, [], []
        cfg_limited = _limit_groq_workers(cfg)
        prompt_version = int(cfg_limited.get("summary_prompt_version", 7) or 7)
        key = _file_summary_job_key(kind, root_abs, paths, prompt_version)
        if manager:
            fetched, errors = manager.run(
                key,
                ensure_summaries,
                paths,
                kind,
                processor,
                cfg_limited,
                None,
                force=False,
                root=root_abs,
                strict_required=strict_required,
            )
        else:
            fetched, errors = ensure_summaries(
                paths,
                kind,
                processor,
                cfg_limited,
                progress_cb=None,
                force=False,
                root=root_abs,
                strict_required=strict_required,
            )
        merged: Dict[str, Optional[str]] = {}
        for path, items in fetched.items():
            abs_path = os.path.abspath(path)
            merged[path] = items
            merged[abs_path] = items
        missing = []
        for path in paths:
            abs_path = os.path.abspath(path)
            if (
                path not in merged
                and abs_path not in merged
            ) or (merged.get(path) is None and merged.get(abs_path) is None):
                missing.append(path)
        return merged, errors, missing

    short_map, short_errors, short_missing = _fetch("short", short_required)
    long_map, long_errors, long_missing = _fetch("long", long_required)
    summaries_short.update(short_map)
    summaries_long.update(long_map)
    errors_all.extend(short_errors)
    errors_all.extend(long_errors)

    if strict_required and (errors_all or short_missing or long_missing):
        detail = []
        if errors_all:
            detail.append(f"errors: {errors_all}")
        if short_missing:
            detail.append(f"missing_short: {short_missing}")
        if long_missing:
            detail.append(f"missing_long: {long_missing}")
        raise RuntimeError("Summary required but unavailable (" + "; ".join(detail) + ")")

    return summaries_short, summaries_long, errors_all


def _build_plan_for_snapshot(
    ctx: ServerContext,
    files: Iterable[str],
    cfg: Dict[str, Any],
    snapshot: JobInputSnapshot | None,
    *,
    default_override: object | None,
) -> Dict[str, FileMode]:
    if snapshot is None:
        return ctx.core.build_plan(files, default_override=default_override)
    default_mode = ctx.core.coerce_file_mode(default_override)
    if default_mode is None:
        default_mode = CoreController._resolve_default_mode(cfg.get("summary_default_mode", "CODE"))
    folder_prefixes = CoreController._summary_prefixes(snapshot.per_folder_modes)
    plan: Dict[str, FileMode] = {}
    for raw_path in files:
        try:
            abs_path = os.path.abspath(raw_path)
        except Exception:
            abs_path = str(raw_path)
        explicit = snapshot.per_file_modes.get(abs_path)
        if explicit is not None:
            plan[abs_path] = explicit
            continue
        if any(abs_path.startswith(pref) for pref in folder_prefixes):
            plan[abs_path] = FileMode.EXCLUDE
        else:
            plan[abs_path] = default_mode
    return plan


def _prepare_merge_context(
    *,
    ctx: ServerContext,
    base: Path,
    files_abs: list[str],
    payload: Dict[str, Any],
    cfg: Dict[str, Any],
    ensure_summaries: bool = True,
    snapshot: JobInputSnapshot | None = None,
) -> Dict[str, Any]:
    if snapshot is None:
        ctx.core.configure(cfg)
        scan_ctx = ctx.core.scan_ctx
        folder_modes = ctx.core.folder_modes_snapshot()
    else:
        scan_ctx = snapshot.scan_ctx
        folder_modes = dict(snapshot.per_folder_modes)
    if scan_ctx is None:
        raise RuntimeError("Scan context missing; run a scan first.")

    root_abs = str(scan_ctx.root)
    try:
        if Path(root_abs).resolve() != base.resolve():
            raise RuntimeError("Scan context root does not match path; run /api/scan")
    except Exception as exc:
        raise RuntimeError("Invalid scan context; run /api/scan") from exc
    content_override = None
    if "content_mode" in payload:
        content_override = _normalize_content_mode(
            payload.get("content_mode"),
            default="code_only",
        )
    plan = _build_plan_for_snapshot(ctx, files_abs, cfg, snapshot, default_override=content_override)
    content_mode_map = _content_mode_map_from_plan(plan)
    files_for_merge = [p for p in files_abs if plan.get(p) != FileMode.EXCLUDE]

    strict_required = bool(cfg.get("strict_summary_required", False))
    cfg_limited = _limit_groq_workers(cfg)

    summarize_vendored = bool(cfg.get("summarize_vendored", False))
    if not summarize_vendored:
        summary_modes = {
            FileMode.SUMMARY_SHORT,
            FileMode.SUMMARY_LONG,
            FileMode.CODE_PLUS_SHORT,
            FileMode.CODE_PLUS_LONG,
            FileMode.SUMMARY_SHORT_PLUS_SKELETON,
            FileMode.SUMMARY_LONG_PLUS_SKELETON,
            FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
            FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
        }
        rel_map: dict[str, str] = {}
        rels: list[str] = []
        for abs_path, mode in plan.items():
            if mode not in summary_modes:
                continue
            try:
                rel = os.path.relpath(abs_path, root_abs)
            except Exception:
                continue
            rels.append(rel)
            rel_map[rel] = abs_path
        if rels:
            vendored = ctx.processor.git_vendored_set(root_abs, rels)
            for rel in vendored:
                abs_path = rel_map.get(rel)
                if not abs_path:
                    continue
                if plan.get(abs_path) in {
                    FileMode.SUMMARY_SHORT_PLUS_SKELETON,
                    FileMode.SUMMARY_LONG_PLUS_SKELETON,
                }:
                    plan[abs_path] = FileMode.SKELETON
                    content_mode_map[abs_path] = "skeleton"
                else:
                    plan[abs_path] = FileMode.CODE
                    content_mode_map[abs_path] = "code"

    if not ensure_summaries:
        short_required = [
            p
            for p, m in plan.items()
            if m in (
                FileMode.SUMMARY_SHORT,
                FileMode.CODE_PLUS_SHORT,
                FileMode.SUMMARY_SHORT_PLUS_SKELETON,
                FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
            )
        ]
        long_required = [
            p
            for p, m in plan.items()
            if m in (
                FileMode.SUMMARY_LONG,
                FileMode.CODE_PLUS_LONG,
                FileMode.SUMMARY_LONG_PLUS_SKELETON,
                FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
            )
        ]
        summaries_short = load_cached_summaries(short_required, "short", cfg)
        summaries_long = load_cached_summaries(long_required, "long", cfg)
        summary_errors: list[str] = []
        if strict_required:
            missing: list[str] = []
            for path in short_required:
                abs_path = os.path.abspath(path)
                if (
                    summaries_short.get(path) is None
                    and summaries_short.get(abs_path) is None
                ):
                    missing.append(path)
            for path in long_required:
                abs_path = os.path.abspath(path)
                if (
                    summaries_long.get(path) is None
                    and summaries_long.get(abs_path) is None
                ):
                    missing.append(path)
            if missing:
                raise RuntimeError(
                    "Summary required but unavailable (" + "; ".join(sorted(set(missing))) + ")"
                )
    else:
        summaries_short, summaries_long, summary_errors = _ensure_required_summaries(
            plan=plan,
            processor=ctx.processor,
            cfg=cfg,
            root_abs=root_abs,
            strict_required=strict_required,
            manager=ctx.summarization_manager,
        )

    folder_mode_map = {
        p: str(m or "").strip().upper()
        for p, m in folder_modes.items()
        if str(m or "").strip().upper().startswith("FOLDER_SUMMARY")
    }
    folder_targets = [p for p in folder_mode_map.keys() if _is_under_root(p, root_abs)]
    folder_targets = _dedupe_folder_targets(folder_targets)
    folder_mode_map = {p: folder_mode_map.get(p, "FOLDER_SUMMARY_QUICK") for p in folder_targets}

    include_repo = payload.get("include_repo_summary")
    if include_repo is None:
        include_repo = bool(cfg.get("include_repo_summary_in_merge", False))
    include_repo = bool(include_repo)
    repo_mode = payload.get("include_repo_summary_mode")
    if repo_mode is None:
        repo_mode = cfg.get("include_repo_summary_mode", cfg.get("repo_summary_mode", "quick"))
    repo_mode = _summary_mode_or_default(repo_mode, default="quick")

    virtual_blocks: list[dict] = []
    summarized_folder_rel_paths: dict[str, str] = {}
    aggregate_scan_files: list[FileMeta] | None = None
    if folder_targets or include_repo:
        aggregate_scan_files = _scan_files_for_aggregate(
            ctx=ctx,
            root_abs=root_abs,
            cfg=cfg_limited,
            payload=payload,
            scan_ctx=scan_ctx,
        )

    if include_repo:
        repo_scan_files = list(aggregate_scan_files or [])
        if not repo_scan_files:
            raise RuntimeError("Repository summary scope is empty after exclusions.")
        prompt_version = int(cfg_limited.get("summary_prompt_version", 7) or 7)
        repo_model, repo_runtime_fingerprint = _summary_job_runtime_contract(
            target_kind="repo",
            mode=repo_mode,
            cfg=cfg_limited,
        )
        key = _repo_summary_job_key(
            root_abs,
            prompt_version,
            repo_mode,
            model_name=repo_model,
            runtime_fingerprint=repo_runtime_fingerprint,
            paths=[str(meta.abs_path) for meta in repo_scan_files],
        )
        obj = ctx.summarization_manager.run(
            key,
            ensure_repo_summary,
            root_abs=root_abs,
            scan_files=list(repo_scan_files),
            file_processor=ctx.processor,
            app_config=cfg_limited,
            mode=repo_mode,
            scope_source="aggregate_scan",
        )
        text = render_repo_summary(obj, mode=repo_mode, profile="context_pack")
        virtual_blocks.append(
            {"kind": "repo", "display": root_abs, "text": text, "variant": str(repo_mode or "quick")}
        )

    for folder_abs in folder_targets:
        folder_mode = folder_mode_map.get(folder_abs, "FOLDER_SUMMARY_QUICK")
        folder_variant = "full" if folder_mode == "FOLDER_SUMMARY_FULL" else "quick"
        prompt_version = int(cfg_limited.get("summary_prompt_version", 7) or 7)
        folder_model, folder_runtime_fingerprint = _summary_job_runtime_contract(
            target_kind="folder",
            mode=folder_variant,
            cfg=cfg_limited,
        )
        key = _folder_summary_job_key_with_scope(
            folder_abs,
            root_abs,
            prompt_version,
            folder_variant,
            model_name=folder_model,
            runtime_fingerprint=folder_runtime_fingerprint,
            paths=[str(meta.abs_path) for meta in list(aggregate_scan_files or [])],
        )
        obj = ctx.summarization_manager.run(
            key,
            ensure_folder_summary,
            folder_abs=folder_abs,
            root_abs=root_abs,
            scan_files=list(aggregate_scan_files or []),
            file_processor=ctx.processor,
            app_config=cfg_limited,
            mode=folder_variant,
            scope_source="aggregate_scan",
        )
        text = render_folder_summary(folder_abs, obj, mode=folder_variant, profile="context_pack")
        virtual_blocks.append(
            {"kind": "folder", "display": folder_abs, "text": text, "variant": folder_variant}
        )
        summarized_folder_rel_paths[_rel_path_for_tree(folder_abs, root_abs)] = folder_mode

    pinned_file_rel_paths = [
        _rel_path_for_tree(path, root_abs) for path in files_for_merge
    ]

    return {
        "root_abs": root_abs,
        "plan": plan,
        "files_for_merge": files_for_merge,
        "content_mode_map": content_mode_map,
        "summaries_short": summaries_short,
        "summaries_long": summaries_long,
        "summary_errors": summary_errors,
        "virtual_blocks": virtual_blocks,
        "summarized_folder_rel_paths": summarized_folder_rel_paths,
        "pinned_file_rel_paths": pinned_file_rel_paths,
        "strict_required": strict_required,
    }

def _compute_excluded_dirs(
    cfg: Dict[str, Any],
    payload: Dict[str, Any] | None,
    *,
    processor: FileProcessor,
) -> set[str]:
    raw_excluded = cfg.get("excluded_dirs") if "excluded_dirs" in cfg else None
    if raw_excluded is None:
        try:
            base_excluded_dirs = set(getattr(processor, "default_excluded_dirs", set()))
        except Exception:
            base_excluded_dirs = set()
    elif not isinstance(raw_excluded, (list, set, tuple)):
        try:
            base_excluded_dirs = set(getattr(processor, "default_excluded_dirs", set()))
        except Exception:
            base_excluded_dirs = set()
    else:
        base_excluded_dirs = _norm_str_set(raw_excluded)
    merged_cfg = dict(cfg)
    if isinstance(payload, dict):
        merged_cfg.update(payload)
    return scan_service.compute_noise_exclusions(base_excluded_dirs, merged_cfg)


def _scan_files_for_aggregate(
    *,
    ctx: ServerContext,
    root_abs: str,
    cfg: Dict[str, Any],
    payload: Dict[str, Any] | None,
    scan_ctx: ScanContext | None = None,
) -> list[FileMeta]:
    scan_ctx = scan_ctx if scan_ctx is not None else ctx.core.scan_ctx
    reuse_scan_ctx = not (
        isinstance(payload, dict) and (_payload_show_all(payload) or payload.get("include_all") is True)
    )
    return scan_aggregate_scope_files(
        root_abs=root_abs,
        file_processor=ctx.processor,
        app_config=cfg,
        config_overrides=payload,
        base_scan_ctx=scan_ctx,
        allow_scan_ctx_reuse=reuse_scan_ctx,
    )


# ----------------- Scan endpoint -----------------
def _scan_visible(
    base: Path,
    payload: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    manual_lookup: set[str] | None = None,
    progress_cb: ProgressCb | None = None,
) -> tuple[object, UiState, list[FileMeta], set[str], list[str], set[str]]:
    """Run a scan+filter pass matching the JS UI scan endpoint.

    Returns: (scan_ctx, ui_state, visible_files, duplicate_set, warnings)
    """
    ui_warnings: list[str] = []
    show_all = _payload_show_all(payload)
    ui = _payload_to_ui_state(payload, cfg, warnings=ui_warnings)

    srv = _require_ctx()
    srv.core.configure(cfg)

    manual_lookup = set(manual_lookup) if manual_lookup is not None else _manual_lookup_snapshot()
    if manual_lookup is not None:
        srv.core.clear_manual_includes()
        srv.core.add_manual_includes(manual_lookup)

    ignore_persisted = bool(payload.get("ignore_custom_excluded", False)) or show_all
    custom_excluded = set() if ignore_persisted else None

    ctx = srv.core.scan(
        ui,
        custom_excluded=custom_excluded,
        progress_cb=progress_cb,
        ignore_excludes=show_all,
    )
    result = srv.core.apply_filters(ui, custom_excluded=custom_excluded)

    combined_warnings = [*ui_warnings, *(result.warnings or [])]
    pattern_hits = set(result.pattern_hits or set())
    return ctx, ui, result.visible_files, result.duplicate_set, combined_warnings, pattern_hits


def _file_types_snapshot(cfg: Dict[str, Any]) -> Dict[str, bool]:
    file_types_for_ui: Dict[str, bool] = {}
    try:
        ft_raw = cfg.get("file_types", {})
        if isinstance(ft_raw, dict):
            file_types_for_ui = {
                str(k): bool(v)
                for k, v in ft_raw.items()
                if isinstance(k, str)
            }
    except Exception:
        file_types_for_ui = {}
    return file_types_for_ui


def _refresh_file_types_for_ui(
    base: Path,
    ui: UiState,
    cfg: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, bool]:
    srv = _require_ctx()
    file_types_for_ui: Dict[str, bool] = {}
    try:
        excluded_dirs = _compute_excluded_dirs(cfg, payload, processor=srv.processor)
        excluded_files = _effective_excluded_files(cfg)
        scan_host = _ScanHostView(srv.processor, excluded_dirs=excluded_dirs, excluded_files=excluded_files)
        discovered = discover_extensions(
            scan_host,
            str(base),
            max_depth=ui.scan.max_depth,
            follow_symlinks=ui.scan.follow_symlinks,
        )
        with srv.config_lock:
            cfg_now = srv.config.get_config()
            ft_raw = cfg_now.get("file_types", {})
            current = ft_raw if isinstance(ft_raw, dict) else {}
            new_exts = sorted(e for e in discovered if e not in current)
            if new_exts:
                srv.config.update_config({"file_types": {ext: False for ext in new_exts}})
                cfg_now = srv.config.get_config()
                ft_raw = cfg_now.get("file_types", {})
            if isinstance(ft_raw, dict):
                file_types_for_ui = {
                    str(k): bool(v)
                    for k, v in ft_raw.items()
                    if isinstance(k, str)
                }
    except Exception:
        file_types_for_ui = _file_types_snapshot(cfg)
    return file_types_for_ui


def _build_scan_response(
    ctx: object,
    *,
    ui: UiState,
    visible: list[FileMeta],
    dup_set: set[str],
    warnings: list[str],
    manual_lookup: set[str],
    cfg: Dict[str, Any],
    file_types_for_ui: Dict[str, bool],
    pattern_hits: set[str] | None = None,
) -> Dict[str, Any]:
    srv = _require_ctx()
    srv.core.configure(cfg)
    root = getattr(ctx, "root", ui.root_dir)
    files_json = [_serialize_file(m, root, manual_lookup=manual_lookup, dup_set=dup_set) for m in visible]
    default_mode = srv.core.default_file_mode()
    for item in files_json:
        abs_path = item.get("abs_path")
        if isinstance(abs_path, str) and abs_path:
            try:
                item["mode"] = srv.core.get_mode(abs_path).name
            except Exception:
                item["mode"] = default_mode.name
    warnings_out = list(warnings) if warnings else []
    try:
        read_errors = sum(1 for meta in visible if getattr(meta, "read_error", False))
        if read_errors:
            warnings_out.append(f"Warning: {read_errors} file(s) unreadable; token count unavailable.")
    except Exception:
        pass
    resp = {
        "root": str(root),
        "files": files_json,
        "aggregate_tokens": int(sum(m.token_count or 0 for m in visible)),
        "aggregate_bytes": int(sum(m.size_bytes for m in visible)),
        "count": len(files_json),
        "default_mode": default_mode.name,
        "file_types": file_types_for_ui,
        "manual_total": len(manual_lookup),
        "folder_modes": srv.core.folder_modes_snapshot(),
        "include_repo_summary_in_merge": bool(cfg.get("include_repo_summary_in_merge", False)),
        "include_repo_summary_mode": cfg.get("include_repo_summary_mode", "quick"),
    }
    if warnings_out:
        resp["warnings"] = warnings_out
    if pattern_hits:
        resp["pattern_hits"] = sorted(pattern_hits)
    return resp


def _serialize_file(
    meta: FileMeta,
    root: Path,
    *,
    manual_lookup: set[str] | None = None,
    dup_set: set[str] | None = None,
) -> Dict[str, Any]:
    abs_str = os.path.abspath(str(meta.abs_path))
    return {
        "abs_path": abs_str,
        "rel_path": str(meta.rel_path),
        "size_bytes": int(meta.size_bytes),
        "mtime": float(meta.mtime),
        "token_count": None if meta.token_count is None else int(meta.token_count),
        "ext": meta.ext,
        "category": meta.category.name,
        "excluded": bool(meta.excluded),
        "read_error": bool(meta.read_error),
        # optional flags added during filtering
        "duplicate": bool(dup_set is not None and abs_str in dup_set),
        "is_manual": bool(manual_lookup is not None and abs_str in manual_lookup),
    }


@app.post("/api/scan", response_model=ScanResponse)
def post_scan(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)

    _ensure_config_for_path(path, ctx=srv)
    cfg = srv.config.get_config()
    manual_lookup = _manual_lookup_snapshot()
    ctx, ui, visible, dup_set, combined_warnings, pattern_hits = _scan_visible(
        base,
        payload,
        cfg,
        manual_lookup=manual_lookup,
    )

    file_types_for_ui = _refresh_file_types_for_ui(base, ui, cfg, payload)
    resp = _build_scan_response(
        ctx,
        ui=ui,
        visible=visible,
        dup_set=dup_set,
        warnings=combined_warnings,
        manual_lookup=manual_lookup,
        cfg=cfg,
        file_types_for_ui=file_types_for_ui,
        pattern_hits=pattern_hits,
    )
    return JSONResponse(resp)


@app.post("/api/filters/apply", response_model=ScanResponse)
def post_apply_filters(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)

    _ensure_config_for_path(path, ctx=srv)
    ctx = srv.core.scan_ctx
    if not ctx or not getattr(ctx, "root", None):
        return JSONResponse({"error": "no scan context; run /api/scan first", "needs_scan": True}, status_code=409)
    try:
        ctx_root = Path(getattr(ctx, "root")).resolve()
        if ctx_root != base.resolve():
            return JSONResponse({"error": "scan context root does not match path; run /api/scan", "needs_scan": True}, status_code=409)
    except Exception:
        return JSONResponse({"error": "invalid scan context; run /api/scan"}, status_code=409)

    cfg = srv.config.get_config()
    manual_lookup = _manual_lookup_snapshot()
    ui_warnings: list[str] = []
    ui = _payload_to_ui_state(payload, cfg, warnings=ui_warnings)
    srv.core.configure(cfg)

    show_all = _payload_show_all(payload)
    ignore_persisted = bool(payload.get("ignore_custom_excluded", False)) or show_all
    custom_excluded = set() if ignore_persisted else None

    result = srv.core.apply_filters(ui, custom_excluded=custom_excluded)
    combined_warnings = [*ui_warnings, *(result.warnings or [])]
    pattern_hits = set(result.pattern_hits or set())
    file_types_for_ui = _file_types_snapshot(cfg)

    resp = _build_scan_response(
        ctx,
        ui=ui,
        visible=result.visible_files,
        dup_set=result.duplicate_set,
        warnings=combined_warnings,
        manual_lookup=manual_lookup,
        cfg=cfg,
        file_types_for_ui=file_types_for_ui,
        pattern_hits=pattern_hits,
    )
    return JSONResponse(resp)


@app.post("/api/scan/clear")
def post_scan_clear(payload: Dict[str, Any] = Body(None)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") if isinstance(payload, dict) else None
    if not isinstance(path, str) or not path:
        ctx = srv.core.scan_ctx
        if ctx and getattr(ctx, "root", None):
            path = str(ctx.root)
        else:
            path = str(srv.config.get_config().get("last_directory", "") or "")
    if isinstance(path, str) and path:
        _ensure_config_for_path(path, ctx=srv)
    cfg = srv.config.get_config()

    try:
        with srv.core._lock:
            ctx = srv.core.scan_ctx
            if ctx is not None:
                try:
                    ctx.files = []
                    ctx.aggregate_tokens = 0
                    ctx.aggregate_bytes = 0
                    ctx.by_ext = {}
                    ctx.by_category = {}
                except Exception:
                    pass
            srv.core.visible_files = []
            srv.core.folder_totals = {}
            srv.core.dup_set = set()
            srv.core.pattern_hits = set()
            try:
                srv.core._token_cache.clear()
            except Exception:
                pass
    except Exception:
        pass

    file_types_for_ui = _file_types_snapshot(cfg)
    manual_lookup = _manual_lookup_snapshot()
    default_mode = srv.core.default_file_mode()
    resp = {
        "root": path or "",
        "files": [],
        "aggregate_tokens": 0,
        "aggregate_bytes": 0,
        "count": 0,
        "default_mode": default_mode.name,
        "file_types": file_types_for_ui,
        "manual_total": len(manual_lookup),
    }
    return JSONResponse(resp)


def _run_scan_job(ctx: ServerContext, job_id: str, base: Path, payload: Dict[str, Any]) -> None:
    try:
        cfg = ctx.config.get_config()
        manual_lookup = _manual_lookup_snapshot()

        def _progress(pct: float, msg: str) -> None:
            _jobs_progress(ctx, job_id, msg, pct)

        scan_ctx, ui, visible, dup_set, warnings, pattern_hits = _scan_visible(
            base,
            payload,
            cfg,
            manual_lookup=manual_lookup,
            progress_cb=_progress,
        )
        file_types_for_ui = _refresh_file_types_for_ui(base, ui, cfg, payload)
        resp = _build_scan_response(
            scan_ctx,
            ui=ui,
            visible=visible,
            dup_set=dup_set,
            warnings=warnings,
            manual_lookup=manual_lookup,
            cfg=cfg,
            file_types_for_ui=file_types_for_ui,
            pattern_hits=pattern_hits,
        )
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = resp
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


@app.post("/api/scan_job")
def post_scan_job(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)

    _ensure_config_for_path(path, ctx=srv)
    payload_copy = dict(payload) if isinstance(payload, dict) else {}
    job_id, err = srv.job_service.submit(JobType.SCAN, _run_scan_job, (srv, base, payload_copy))
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id})


# ----------------- Per-file mode overrides -----------------
@app.put("/api/modes")
def put_modes(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    sel = payload.get("selected_paths")
    mode_raw = payload.get("mode")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    if not isinstance(sel, list) or not sel:
        return JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    if not isinstance(mode_raw, str) or not mode_raw.strip():
        return JSONResponse({"error": "mode must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)

    mode_str = mode_raw.strip()
    clear = mode_str.lower() in {"clear", "default", "reset"}
    mode = None if clear else srv.core.coerce_file_mode(mode_str)
    if not clear and mode is None:
        return JSONResponse({"error": f"invalid mode: {mode_raw}"}, status_code=400)

    files_abs: list[str] = []
    for s in sel:
        if not isinstance(s, str) or not s:
            continue
        p = Path(s)
        if not p.is_absolute():
            p = base / s
        try:
            p = p.resolve()
        except Exception:
            pass
        try:
            _ = p.relative_to(base.resolve())
        except Exception:
            return JSONResponse({"error": f"access denied: {s}"}, status_code=403)
        if not p.exists() or not p.is_file():
            continue
        files_abs.append(_canonicalize_path(str(p)))
    files_abs = list(dict.fromkeys(files_abs))
    if not files_abs:
        return JSONResponse({"error": "no valid files"}, status_code=400)

    with srv.config_lock:
        cfg = srv.config.get_config()
        srv.core.configure(cfg)
        srv.core.set_mode(files_abs, None if clear else mode)
        try:
            serialised = srv.processor.save_modes_to_config(cfg, srv.core.per_file_modes)
        except Exception:
            serialised = {p: m.name for p, m in srv.core.per_file_modes.items()}
        cfg["per_file_modes"] = serialised
        try:
            srv.config.save_config()
        except Exception:
            srv.config.update_config({"per_file_modes": serialised})
        cfg = srv.config.get_config()
        srv.core.configure(cfg)

    default_mode = srv.core.default_file_mode()
    effective_modes = [
        srv.core.get_mode(p).name for p in files_abs
    ]
    return JSONResponse(
        {
            "ok": True,
            "updated": len(files_abs),
            "mode": "CLEAR" if clear else (mode.name if mode else None),
            "default_mode": default_mode.name,
            "effective_modes": effective_modes,
        }
    )


@app.post("/api/folder_modes")
def post_folder_modes(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    paths = payload.get("paths")
    mode_raw = payload.get("mode")
    root = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(root, str) or not root:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    if not isinstance(paths, list) or not paths:
        return JSONResponse({"error": "paths must be a non-empty array"}, status_code=400)
    base = Path(root)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {root}"}, status_code=400)
    _ensure_config_for_path(root, ctx=srv)

    mode = None
    if isinstance(mode_raw, str) and mode_raw.strip():
        raw_up = mode_raw.strip().upper()
        if raw_up == "FOLDER_SUMMARY":
            raw_up = "FOLDER_SUMMARY_QUICK"
        if raw_up not in {"FOLDER_SUMMARY_QUICK", "FOLDER_SUMMARY_FULL"}:
            return JSONResponse({"error": f"invalid mode: {mode_raw}"}, status_code=400)
        mode = raw_up

    folders_abs: list[str] = []
    for s in paths:
        if not isinstance(s, str) or not s:
            continue
        p = Path(s)
        if not p.is_absolute():
            p = base / s
        try:
            p = p.resolve()
        except Exception:
            pass
        try:
            _ = p.relative_to(base.resolve())
        except Exception:
            return JSONResponse({"error": f"access denied: {s}"}, status_code=403)
        if not p.exists() or not p.is_dir():
            continue
        folders_abs.append(os.path.abspath(str(p)))
    folders_abs = list(dict.fromkeys(folders_abs))
    if not folders_abs:
        return JSONResponse({"error": "no valid folders"}, status_code=400)

    with srv.config_lock:
        cfg = srv.config.get_config()
        srv.core.configure(cfg)
        srv.core.set_folder_mode(folders_abs, mode)
        cfg["per_folder_modes"] = srv.core.folder_modes_snapshot()
        try:
            srv.config.save_config()
        except Exception:
            srv.config.update_config({"per_folder_modes": cfg["per_folder_modes"]})
        cfg = srv.config.get_config()
        srv.core.configure(cfg)

    return JSONResponse({"ok": True, "folder_modes": srv.core.folder_modes_snapshot()})


@app.post("/api/modes/reset")
def post_modes_reset() -> JSONResponse:
    srv = _require_ctx()
    with srv.config_lock:
        srv.core.per_file_modes = {}
        cfg = srv.config.get_config()
        cfg["per_file_modes"] = {}
        try:
            srv.config.save_config()
        except Exception:
            srv.config.update_config({"per_file_modes": {}})
    return JSONResponse({"ok": True})


def _resolve_output_file(base: Path, payload: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    output_file = payload.get("output_file")
    default_name: str | None = None
    if not isinstance(output_file, str) or not output_file.strip():
        use_folder = bool(cfg.get("use_folder_name", False))
        append_dt = bool(cfg.get("append_datetime", False))
        base_name = base.name if use_folder else "merged_files"
        if append_dt:
            from datetime import datetime as _dt
            base_name = f"{base_name}_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
        default_name = f"{base_name}.txt"
        output_file = None
    policy = _path_policy_for(
        base,
        allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
    )
    resolved = policy.resolve_output_file(output_file, default_name=default_name or "merged_files.txt")
    return str(resolved.path)


def _check_single_output(output_file: str, payload: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    paths = [Path(output_file)]
    if _payload_to_ui_state(payload, cfg).output.json_manifest:
        paths.append(Path(output_file + ".manifest.json"))
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise PathConflict(f"Output must be a regular file: {path.name}")
        if path.exists() and payload.get("overwrite") is not True:
            raise PathConflict(f"Output already exists: {path.name}")


def _resolve_selected_paths(
    payload: Dict[str, Any],
    base: Path,
    srv: "ServerContext",
) -> tuple[list[str] | None, JSONResponse | None]:
    sel = payload.get("selected_paths")
    if sel is None or (isinstance(sel, list) and not sel):
        ctx = srv.core.scan_ctx
        if not ctx or not getattr(ctx, "root", None):
            return None, JSONResponse({"error": "selected_paths required (no scan context to infer selection)"}, status_code=400)
        try:
            if Path(ctx.root).resolve() != base.resolve():
                return None, JSONResponse({"error": "selected_paths required (scan context root mismatch)"}, status_code=400)
        except Exception:
            pass
        visible = list(getattr(srv.core, "visible_files", []) or [])
        if not visible:
            return None, JSONResponse({"error": "selected_paths required (no visible files)"}, status_code=400)
        files_abs: list[str] = []
        try:
            base_resolved = base.resolve()
        except Exception:
            base_resolved = base
        for meta in visible:
            try:
                p = Path(meta.abs_path)
            except Exception:
                continue
            try:
                p = p.resolve()
            except Exception:
                pass
            if not p.exists() or not p.is_file():
                continue
            try:
                _ = p.relative_to(base_resolved)
            except Exception:
                continue
            try:
                if srv.core.get_mode(str(p)) == FileMode.EXCLUDE:
                    continue
            except Exception:
                pass
            files_abs.append(_canonicalize_path(str(p)))
        if not files_abs:
            return None, JSONResponse({"error": "selected_paths required (no mergeable files)"}, status_code=400)
        _seen: set[str] = set()
        files_abs = [x for x in files_abs if not (x in _seen or _seen.add(x))]
        return files_abs, None

    if not isinstance(sel, list) or not sel:
        return None, JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)

    files_abs: list[str] = []
    policy = _path_policy_for(base, ctx=srv)
    for s in sel:
        if not isinstance(s, str):
            return None, JSONResponse({"error": "selected_paths items must be strings"}, status_code=400)
        try:
            resolved = policy.resolve_input_file(s)
        except PathPolicyError as exc:
            return None, _path_error_response(exc)
        files_abs.append(_canonicalize_path(str(resolved.path)))
    _seen: set[str] = set()
    files_abs = [x for x in files_abs if not (x in _seen or _seen.add(x))]
    if not files_abs:
        return None, JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    return files_abs, None


def _resolve_input_file_values(
    values: object,
    base: Path,
    srv: "ServerContext",
    *,
    field_name: str,
    empty_error: str | None = None,
) -> tuple[list[str] | None, JSONResponse | None]:
    if not isinstance(values, list) or not values:
        return None, JSONResponse({"error": empty_error or f"{field_name} must be a non-empty array"}, status_code=400)
    files_abs: list[str] = []
    policy = _path_policy_for(base, ctx=srv)
    for item in values:
        if not isinstance(item, str):
            return None, JSONResponse({"error": f"{field_name} items must be strings"}, status_code=400)
        try:
            resolved = policy.resolve_input_file(item)
        except PathPolicyError as exc:
            return None, _path_error_response(exc)
        files_abs.append(_canonicalize_path(str(resolved.path)))
    seen: set[str] = set()
    files_abs = [path for path in files_abs if not (path in seen or seen.add(path))]
    if not files_abs:
        return None, JSONResponse({"error": empty_error or f"{field_name} must be a non-empty array"}, status_code=400)
    return files_abs, None


def _resolve_output_folder(
    base: Path,
    payload: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    suffix: str,
) -> str:
    policy = _path_policy_for(
        base,
        allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
    )
    output_folder = payload.get("output_folder")
    if isinstance(output_folder, str) and output_folder.strip():
        resolved = policy.resolve_output_folder(
            output_folder,
            default_name=f"merged_files{suffix}",
        )
        return str(resolved.path)

    output_file = payload.get("output_file")
    if isinstance(output_file, str) and output_file.strip():
        resolved_file = policy.resolve_output_file(output_file, default_name="merged_files.txt")
        base_name = os.path.splitext(str(resolved_file.path))[0]
    else:
        base_name = os.path.splitext(_resolve_output_file(base, payload, cfg))[0]
    return f"{base_name}{suffix}"


def _assert_safe_output_folder_overwrite(
    base: Path,
    payload: Dict[str, Any],
    output_folder: str,
) -> None:
    if not _safe_bool(payload.get("overwrite", False)):
        if Path(output_folder).exists():
            raise PathConflict(f"Output folder already exists: {Path(output_folder).name}")
        return
    policy = _path_policy_for(
        base,
        allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
    )
    resolved = policy.resolve_output_folder(output_folder, default_name=Path(output_folder).name)
    policy.assert_safe_overwrite_folder(resolved)


def _mark_output_folder(output_folder: str) -> None:
    try:
        folder = Path(output_folder).resolve(strict=False)
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder / OUTPUT_MARKER
        marker.touch(exist_ok=True)
        _allow_path(folder)
    except Exception:
        logger.debug("failed to mark output folder", exc_info=True)


# ----------------- Merge endpoint -----------------
@app.post("/api/merge")
def post_merge(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)

    cfg = srv.config.get_config()
    preflight = _merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload)
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate

    # Output file: use provided or generate from settings
    try:
        output_file = _resolve_output_file(base, payload, cfg)
        _check_single_output(output_file, payload, cfg)
    except PathPolicyError as exc:
        return _path_error_response(exc)

    try:
        payload_ui = dict(payload)
        payload_ui["output_file"] = output_file
        ui = _payload_to_ui_state(payload_ui, cfg)
        ctx_data = _prepare_merge_context(
            ctx=srv,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
        )
        merge_basic_mod.merge_files(
            srv.processor,
            directory=str(base),
            files_to_merge=ctx_data["files_for_merge"],
            output_file=output_file,
            progress_callback=None,
            include_file_tree=ui.output.include_tree,
            include_summaries=False,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            create_json_manifest=ui.output.json_manifest,
            summaries_by_file=None,
            summaries_short=ctx_data["summaries_short"],
            summaries_long=ctx_data["summaries_long"],
            strict_summary_required=ctx_data["strict_required"],
            content_mode="code_only",
            files_with_summary_errors=ctx_data["summary_errors"],
            prompt_template=ui.summary.prompt_template,
            output_stream=None,
            overwrite=payload.get("overwrite") is True,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            why_by_file=None,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            format_summary=_format_summary,
            content_mode_map=ctx_data["content_mode_map"],
            fallback_to_code_on_summary_error=bool(
                cfg.get("fallback_to_code_on_summary_error", False)
            ),
            virtual_blocks=ctx_data["virtual_blocks"],
            summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
            pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
        )
        files_with_summary_errors = ctx_data["summary_errors"]
        summary_error = None
    except Exception as exc:
        logger.exception("run_merge_single failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    try:
        import os
        size_bytes = os.path.getsize(output_file)
        with open(output_file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        token_count = srv.processor.count_tokens(text)
    except Exception:
        size_bytes = 0
        token_count = None

    # Allow this output for downloading
    _allow_path(output_file)

    resp = {
        "output_path": output_file,
        "size_bytes": size_bytes,
        "token_count": token_count,
    }
    if files_with_summary_errors:
        resp["summary_files_with_errors"] = files_with_summary_errors
    if summary_error:
        resp["summary_error"] = summary_error
    return JSONResponse(resp)


@app.post("/api/merge/preview")
def post_merge_preview(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)

    cfg = srv.config.get_config()
    preflight = _merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload)
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate
    try:
        ui = _payload_to_ui_state(payload, cfg)
        ctx_data = _prepare_merge_context(
            ctx=srv,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
        )
        buf = StringIO()
        merge_basic_mod.merge_files(
            srv.processor,
            directory=str(base),
            files_to_merge=ctx_data["files_for_merge"],
            output_file=os.devnull,
            progress_callback=None,
            include_file_tree=ui.output.include_tree,
            include_summaries=False,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            create_json_manifest=False,
            summaries_by_file=None,
            summaries_short=ctx_data["summaries_short"],
            summaries_long=ctx_data["summaries_long"],
            strict_summary_required=ctx_data["strict_required"],
            content_mode="code_only",
            files_with_summary_errors=ctx_data["summary_errors"],
            prompt_template=ui.summary.prompt_template,
            output_stream=buf,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            why_by_file=None,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            format_summary=_format_summary,
            content_mode_map=ctx_data["content_mode_map"],
            fallback_to_code_on_summary_error=bool(
                cfg.get("fallback_to_code_on_summary_error", False)
            ),
            virtual_blocks=ctx_data["virtual_blocks"],
            summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
            pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
        )
        text = buf.getvalue()
    except Exception as exc:
        logger.exception("preview_merge_text failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    tokens_total = None
    tokens_content_only = None
    try:
        content_mode_map = ctx_data["content_mode_map"]
        _total, content_only = srv.processor.merge_group_token_details(
            directory=str(base),
            files=ctx_data["files_for_merge"],
            include_file_tree=ui.output.include_tree,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            prompt_template=ui.summary.prompt_template,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            content_mode="code_only",
            summaries_by_file=None,
            summaries_short=ctx_data["summaries_short"],
            summaries_long=ctx_data["summaries_long"],
            content_mode_map=content_mode_map,
            strict_summary_required=ctx_data["strict_required"],
            fallback_to_code_on_summary_error=bool(
                cfg.get("fallback_to_code_on_summary_error", False)
            ),
            virtual_blocks=ctx_data["virtual_blocks"],
            summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
            pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
        )
        tokens_total = int(srv.processor.count_tokens(text))
        tokens_content_only = int(content_only)
    except Exception:
        try:
            tokens_total = int(srv.processor.count_tokens(text))
        except Exception:
            tokens_total = None

    resp = {"text": text}
    if tokens_total is not None:
        resp["tokens_total"] = tokens_total
    if tokens_content_only is not None:
        resp["tokens_content_only"] = tokens_content_only
    return JSONResponse(resp)


@app.post("/api/merge/multiple")
def post_merge_multiple(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)

    cfg = srv.config.get_config()
    preflight = _merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload)
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate
    split_into_n = int(payload.get("split_into_n", cfg.get("split_into_n", 5)))
    overwrite = _safe_bool(payload.get("overwrite", False))

    try:
        output_folder = _resolve_output_folder(base, payload, cfg, suffix="_split")
        _assert_safe_output_folder_overwrite(base, payload, output_folder)
    except PathPolicyError as exc:
        return _path_error_response(exc)

    try:
        ui = _payload_to_ui_state(payload, cfg)
        ctx_data = _prepare_merge_context(
            ctx=srv,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
        )
        with staged_output_directory(output_folder, overwrite=overwrite) as staged:
            srv.processor.merge_files_into_multiple(
                directory=str(base),
                files_to_merge=ctx_data["files_for_merge"],
                output_folder=str(staged),
                num_text_files=max(1, split_into_n),
                progress_callback=None,
                custom_start_label=ui.output.custom_start,
                custom_end_label=ui.output.custom_end,
                file_tree_start_label=ui.output.tree_start,
                file_tree_end_label=ui.output.tree_end,
                include_file_tree=ui.output.include_tree,
                create_json_manifest=ui.output.json_manifest,
                summaries_by_file=None,
                summaries_short=ctx_data["summaries_short"],
                summaries_long=ctx_data["summaries_long"],
                content_mode="code_only",
                files_with_summary_errors=ctx_data["summary_errors"],
                prompt_template=ui.summary.prompt_template,
                verb="Splitting",
                remove_empty=ui.filters.remove_empty,
                enumerate_lines=ui.filters.enumerate_lines,
                content_mode_map=ctx_data["content_mode_map"],
                fallback_to_code_on_summary_error=bool(
                    cfg.get("fallback_to_code_on_summary_error", False)
                ),
                strict_summary_required=ctx_data["strict_required"],
                virtual_blocks=ctx_data["virtual_blocks"],
                summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
                pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
            )
            staged.mkdir(exist_ok=True)
            (staged / OUTPUT_MARKER).touch()
        files_with_summary_errors = ctx_data["summary_errors"]
        summary_error = None
    except Exception as e:
        logger.exception("run_merge_split_lines failed")
        return JSONResponse({"error": str(e)}, status_code=500)

    _mark_output_folder(output_folder)
    resp = {"output_folder": output_folder}
    if files_with_summary_errors:
        resp["summary_files_with_errors"] = files_with_summary_errors
    if summary_error:
        resp["summary_error"] = summary_error
    return JSONResponse(resp)


@app.post("/api/merge/token_split")
def post_merge_token_split(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)

    cfg = srv.config.get_config()
    preflight = _merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload)
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate
    token_limit = int(payload.get("token_limit", cfg.get("token_limit", 1_048_576)))
    overwrite = _safe_bool(payload.get("overwrite", False))

    leq = "\u2264"
    try:
        output_folder = _resolve_output_folder(base, payload, cfg, suffix=f"_{leq}{token_limit}_tok")
        _assert_safe_output_folder_overwrite(base, payload, output_folder)
    except PathPolicyError as exc:
        return _path_error_response(exc)

    try:
        ui = _payload_to_ui_state(payload, cfg)
        ctx_data = _prepare_merge_context(
            ctx=srv,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
        )
        with staged_output_directory(output_folder, overwrite=overwrite) as staged:
            srv.processor.merge_files_token_split(
                directory=str(base),
                files_to_merge=ctx_data["files_for_merge"],
                output_folder=str(staged),
                token_limit=max(1, token_limit),
                progress_callback=None,
                custom_start_label=ui.output.custom_start,
                custom_end_label=ui.output.custom_end,
                file_tree_start_label=ui.output.tree_start,
                file_tree_end_label=ui.output.tree_end,
                include_file_tree=ui.output.include_tree,
                create_json_manifest=ui.output.json_manifest,
                summaries_by_file=None,
                summaries_short=ctx_data["summaries_short"],
                summaries_long=ctx_data["summaries_long"],
                content_mode="code_only",
                files_with_summary_errors=ctx_data["summary_errors"],
                prompt_template=ui.summary.prompt_template,
                verb="Splitting",
                remove_empty=ui.filters.remove_empty,
                enumerate_lines=ui.filters.enumerate_lines,
                why_by_file=None,
                content_mode_map=ctx_data["content_mode_map"],
                fallback_to_code_on_summary_error=bool(
                    cfg.get("fallback_to_code_on_summary_error", False)
                ),
                strict_summary_required=ctx_data["strict_required"],
                virtual_blocks=ctx_data["virtual_blocks"],
                summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
                pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
            )
            staged.mkdir(exist_ok=True)
            (staged / OUTPUT_MARKER).touch()
        files_with_summary_errors = ctx_data["summary_errors"]
    except Exception as e:
        logger.exception("run_merge_split_tokens failed")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Allow the output folder for download
    _mark_output_folder(output_folder)
    resp = {"output_folder": output_folder}
    if files_with_summary_errors:
        resp["summary_files_with_errors"] = files_with_summary_errors
    return JSONResponse(resp)


def _resolve_selected_files(
    base: Path,
    sel: object,
    *,
    srv: "ServerContext" | None = None,
    payload: Dict[str, Any] | None = None,
) -> list[str]:
    if srv is not None and payload is not None:
        files_abs, err = _resolve_selected_paths(payload, base, srv)
        if err is not None or not files_abs:
            msg = "selected_paths must be a non-empty array"
            if isinstance(err, JSONResponse):
                try:
                    body = json.loads(err.body.decode("utf-8"))
                    msg = body.get("error") or msg
                except Exception:
                    pass
            raise ValueError(msg)
        return files_abs
    if not isinstance(sel, list) or not sel:
        raise ValueError("selected_paths must be a non-empty array")
    files_abs: list[str] = []
    for s in sel:
        if not isinstance(s, str):
            raise ValueError("selected_paths items must be strings")
        p = Path(s)
        if not p.is_absolute():
            p = base / s
        try:
            p = p.resolve()
        except Exception:
            pass
        if not p.exists() or not p.is_file():
            raise ValueError(f"file not found: {s}")
        files_abs.append(_canonicalize_path(str(p)))
    _seen: set[str] = set()
    return [x for x in files_abs if not (x in _seen or _seen.add(x))]


@app.post("/api/merge/token_split/check")
def post_merge_token_split_check(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    cfg = srv.config.get_config()
    token_limit = int(payload.get("token_limit", cfg.get("token_limit", 1_048_576)))
    leq = "\u2264"
    try:
        output_folder = _resolve_output_folder(base, payload, cfg, suffix=f"_{leq}{token_limit}_tok")
    except PathPolicyError as exc:
        return _path_error_response(exc)
    ui = _payload_to_ui_state(payload, cfg)

    srv.core.configure(cfg)
    content_override = None
    if "content_mode" in payload:
        content_override = _normalize_content_mode(
            payload.get("content_mode"),
            default="code_only",
        )
    plan = srv.core.build_plan(files_abs, default_override=content_override)
    files_for_merge = [p for p in files_abs if plan.get(p) != FileMode.EXCLUDE]

    largest = 0
    over_limit = 0
    for fpath in files_for_merge:
        try:
            count = srv.processor.processed_token_count(
                fpath,
                enumerate_lines=ui.filters.enumerate_lines,
                remove_empty=ui.filters.remove_empty,
            )
        except Exception:
            count = 0
        largest = max(largest, int(count or 0))
        if token_limit and count and count > token_limit:
            over_limit += 1

    return JSONResponse({
        "files": len(files_for_merge),
        "token_limit": token_limit,
        "largest_token_count": largest,
        "over_limit_count": over_limit,
        "output_folder": output_folder,
    })


def _run_merge_single_job(
    ctx: ServerContext,
    job_id: str,
    base: Path,
    payload: Dict[str, Any],
    snapshot: JobInputSnapshot | None = None,
) -> None:
    try:
        cfg = copy.deepcopy(snapshot.config) if snapshot is not None else ctx.config.get_config()
        files_abs = _resolve_selected_files(base, payload.get("selected_paths"), srv=ctx, payload=payload)

        def _progress(pct: float, msg: str) -> None:
            _jobs_progress(ctx, job_id, msg, pct)

        output_file = _resolve_output_file(base, payload, cfg)
        _check_single_output(output_file, payload, cfg)
        payload_ui = dict(payload)
        payload_ui["output_file"] = output_file
        ui = _payload_to_ui_state(payload_ui, cfg)
        ctx_data = _prepare_merge_context(
            ctx=ctx,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
            snapshot=snapshot,
        )
        merge_basic_mod.merge_files(
            ctx.processor,
            directory=str(base),
            files_to_merge=ctx_data["files_for_merge"],
            output_file=output_file,
            progress_callback=_progress,
            include_file_tree=ui.output.include_tree,
            include_summaries=False,
            custom_start_label=ui.output.custom_start,
            custom_end_label=ui.output.custom_end,
            file_tree_start_label=ui.output.tree_start,
            file_tree_end_label=ui.output.tree_end,
            create_json_manifest=ui.output.json_manifest,
            summaries_by_file=None,
            summaries_short=ctx_data["summaries_short"],
            summaries_long=ctx_data["summaries_long"],
            strict_summary_required=ctx_data["strict_required"],
            content_mode="code_only",
            files_with_summary_errors=ctx_data["summary_errors"],
            prompt_template=ui.summary.prompt_template,
            output_stream=None,
            overwrite=payload.get("overwrite") is True,
            remove_empty=ui.filters.remove_empty,
            enumerate_lines=ui.filters.enumerate_lines,
            why_by_file=None,
            markdown_fences=ui.output.markdown_fences,
            markdown_toc=ui.output.markdown_toc,
            format_summary=_format_summary,
            content_mode_map=ctx_data["content_mode_map"],
            fallback_to_code_on_summary_error=bool(
                cfg.get("fallback_to_code_on_summary_error", False)
            ),
            virtual_blocks=ctx_data["virtual_blocks"],
            summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
            pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
        )
        try:
            size_bytes = os.path.getsize(output_file)
            with open(output_file, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            token_count = ctx.processor.count_tokens(text)
        except Exception:
            size_bytes = 0
            token_count = None

        _allow_path(output_file)
        resp = {
            "output_path": output_file,
            "size_bytes": size_bytes,
            "token_count": token_count,
        }
        if ctx_data["summary_errors"]:
            resp["summary_files_with_errors"] = ctx_data["summary_errors"]
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = resp
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


def _run_merge_split_lines_job(
    ctx: ServerContext,
    job_id: str,
    base: Path,
    payload: Dict[str, Any],
    snapshot: JobInputSnapshot | None = None,
) -> None:
    try:
        cfg = copy.deepcopy(snapshot.config) if snapshot is not None else ctx.config.get_config()
        files_abs = _resolve_selected_files(base, payload.get("selected_paths"), srv=ctx, payload=payload)
        split_into_n = int(payload.get("split_into_n", cfg.get("split_into_n", 5)))
        overwrite = _safe_bool(payload.get("overwrite", False))

        def _progress(pct: float, msg: str) -> None:
            _jobs_progress(ctx, job_id, msg, pct)

        output_folder = _resolve_output_folder(base, payload, cfg, suffix="_split")
        _assert_safe_output_folder_overwrite(base, payload, output_folder)
        ui = _payload_to_ui_state(payload, cfg)
        ctx_data = _prepare_merge_context(
            ctx=ctx,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
            snapshot=snapshot,
        )
        with staged_output_directory(output_folder, overwrite=overwrite) as staged:
            ctx.processor.merge_files_into_multiple(
                directory=str(base),
                files_to_merge=ctx_data["files_for_merge"],
                output_folder=str(staged),
                num_text_files=max(1, split_into_n),
                progress_callback=_progress,
                custom_start_label=ui.output.custom_start,
                custom_end_label=ui.output.custom_end,
                file_tree_start_label=ui.output.tree_start,
                file_tree_end_label=ui.output.tree_end,
                include_file_tree=ui.output.include_tree,
                create_json_manifest=ui.output.json_manifest,
                summaries_by_file=None,
                summaries_short=ctx_data["summaries_short"],
                summaries_long=ctx_data["summaries_long"],
                content_mode="code_only",
                files_with_summary_errors=ctx_data["summary_errors"],
                prompt_template=ui.summary.prompt_template,
                verb="Splitting",
                remove_empty=ui.filters.remove_empty,
                enumerate_lines=ui.filters.enumerate_lines,
                content_mode_map=ctx_data["content_mode_map"],
                fallback_to_code_on_summary_error=bool(
                    cfg.get("fallback_to_code_on_summary_error", False)
                ),
                strict_summary_required=ctx_data["strict_required"],
                virtual_blocks=ctx_data["virtual_blocks"],
                summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
                pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
            )
            staged.mkdir(exist_ok=True)
            (staged / OUTPUT_MARKER).touch()
        files_with_summary_errors = ctx_data["summary_errors"]
        _mark_output_folder(output_folder)
        resp = {"output_folder": output_folder}
        if files_with_summary_errors:
            resp["summary_files_with_errors"] = files_with_summary_errors
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = resp
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


def _run_merge_split_tokens_job(
    ctx: ServerContext,
    job_id: str,
    base: Path,
    payload: Dict[str, Any],
    snapshot: JobInputSnapshot | None = None,
) -> None:
    try:
        cfg = copy.deepcopy(snapshot.config) if snapshot is not None else ctx.config.get_config()
        files_abs = _resolve_selected_files(base, payload.get("selected_paths"), srv=ctx, payload=payload)
        token_limit = int(payload.get("token_limit", cfg.get("token_limit", 1_048_576)))
        overwrite = _safe_bool(payload.get("overwrite", False))
        leq = "\u2264"

        def _progress(pct: float, msg: str) -> None:
            _jobs_progress(ctx, job_id, msg, pct)

        output_folder = _resolve_output_folder(base, payload, cfg, suffix=f"_{leq}{token_limit}_tok")
        _assert_safe_output_folder_overwrite(base, payload, output_folder)
        ui = _payload_to_ui_state(payload, cfg)
        ctx_data = _prepare_merge_context(
            ctx=ctx,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
            snapshot=snapshot,
        )
        with staged_output_directory(output_folder, overwrite=overwrite) as staged:
            ctx.processor.merge_files_token_split(
                directory=str(base),
                files_to_merge=ctx_data["files_for_merge"],
                output_folder=str(staged),
                token_limit=max(1, token_limit),
                progress_callback=_progress,
                custom_start_label=ui.output.custom_start,
                custom_end_label=ui.output.custom_end,
                file_tree_start_label=ui.output.tree_start,
                file_tree_end_label=ui.output.tree_end,
                include_file_tree=ui.output.include_tree,
                create_json_manifest=ui.output.json_manifest,
                summaries_by_file=None,
                summaries_short=ctx_data["summaries_short"],
                summaries_long=ctx_data["summaries_long"],
                content_mode="code_only",
                files_with_summary_errors=ctx_data["summary_errors"],
                prompt_template=ui.summary.prompt_template,
                verb="Splitting",
                remove_empty=ui.filters.remove_empty,
                enumerate_lines=ui.filters.enumerate_lines,
                why_by_file=None,
                content_mode_map=ctx_data["content_mode_map"],
                fallback_to_code_on_summary_error=bool(
                    cfg.get("fallback_to_code_on_summary_error", False)
                ),
                strict_summary_required=ctx_data["strict_required"],
                virtual_blocks=ctx_data["virtual_blocks"],
                summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
                pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
            )
            staged.mkdir(exist_ok=True)
            (staged / OUTPUT_MARKER).touch()
        files_with_summary_errors = ctx_data["summary_errors"]
        _mark_output_folder(output_folder)
        resp = {"output_folder": output_folder}
        if files_with_summary_errors:
            resp["summary_files_with_errors"] = files_with_summary_errors
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = resp
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


@app.post("/api/merge_job")
def post_merge_job(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    cfg = srv.config.get_config()
    preflight = _merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload)
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate
    try:
        output_file = _resolve_output_file(base, payload, cfg)
        _check_single_output(output_file, payload, cfg)
    except PathPolicyError as exc:
        return _path_error_response(exc)

    snapshot = _capture_job_input_snapshot(srv, base)
    payload_copy = dict(payload) if isinstance(payload, dict) else {}
    payload_copy["selected_paths"] = files_abs
    job_id, err = srv.job_service.submit(
        JobType.MERGE,
        _run_merge_single_job,
        (srv, base, payload_copy, snapshot),
        metadata={**_job_snapshot_metadata(snapshot), **_provider_job_metadata(preflight)},
    )
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id})


@app.post("/api/merge/multiple_job")
def post_merge_multiple_job(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    cfg = srv.config.get_config()
    preflight = _merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload)
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate
    try:
        output_folder = _resolve_output_folder(base, payload, cfg, suffix="_split")
        _assert_safe_output_folder_overwrite(base, payload, output_folder)
    except PathPolicyError as exc:
        return _path_error_response(exc)

    snapshot = _capture_job_input_snapshot(srv, base)
    payload_copy = dict(payload) if isinstance(payload, dict) else {}
    payload_copy["selected_paths"] = files_abs
    job_id, err = srv.job_service.submit(
        JobType.MERGE_SPLIT_LINES,
        _run_merge_split_lines_job,
        (srv, base, payload_copy, snapshot),
        metadata={**_job_snapshot_metadata(snapshot), **_provider_job_metadata(preflight)},
    )
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id})


@app.post("/api/merge/token_split_job")
def post_merge_token_split_job(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_selected_paths(payload, base, srv)
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    cfg = srv.config.get_config()
    preflight = _merge_provider_preflight(srv, cfg=cfg, base=base, files_abs=files_abs, payload=payload)
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate
    token_limit = int(payload.get("token_limit", cfg.get("token_limit", 1_048_576)))
    leq = "\u2264"
    try:
        output_folder = _resolve_output_folder(base, payload, cfg, suffix=f"_{leq}{token_limit}_tok")
        _assert_safe_output_folder_overwrite(base, payload, output_folder)
    except PathPolicyError as exc:
        return _path_error_response(exc)

    snapshot = _capture_job_input_snapshot(srv, base)
    payload_copy = dict(payload) if isinstance(payload, dict) else {}
    payload_copy["selected_paths"] = files_abs
    job_id, err = srv.job_service.submit(
        JobType.MERGE_TOKEN_SPLIT,
        _run_merge_split_tokens_job,
        (srv, base, payload_copy, snapshot),
        metadata={**_job_snapshot_metadata(snapshot), **_provider_job_metadata(preflight)},
    )
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id})


@app.get("/download", response_model=None)
def download(path: str, download_token: str | None = None):
    ctx = _require_ctx()
    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    if not _is_allowed_download(p):
        return JSONResponse({"error": "access denied"}, status_code=403)
    if not _download_token_valid(p, download_token, ctx=ctx):
        return JSONResponse({"error": "download token required"}, status_code=403)
    return FileResponse(str(p), filename=p.name, media_type="text/plain")


def _resolve_existing_output_folder(raw: object, base: Path) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("folder must be a non-empty string")
    try:
        policy = _path_policy_for(base)
        resolved = policy.resolve_output_folder(raw, default_name="merged_files_split")
        p = resolved.path
    except PathPolicyError as exc:
        raise PermissionError(str(exc)) from exc
    if not p.exists() or not p.is_dir():
        raise ValueError(f"folder is not a directory: {p}")
    if not policy.can_open(p):
        raise PermissionError("access denied")
    return p


@app.post("/api/output/concat", response_model=OutputConcatResponse)
def post_output_concat(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    base = Path(srv.config.get_config().get("last_directory", "") or ".")
    try:
        folder = _resolve_existing_output_folder(payload.get("folder"), base)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    text = merge_service.read_all_txt(str(folder))
    try:
        files = sorted([n for n in os.listdir(folder) if n.endswith(".txt") and not n.startswith("multi-merge-overview")])
    except Exception:
        files = []
    return JSONResponse({"text": text, "files": files, "count": len(files)})


@app.post("/api/output/zip", response_model=OutputZipResponse)
def post_output_zip(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    base = Path(srv.config.get_config().get("last_directory", "") or ".")
    overwrite = _safe_bool(payload.get("overwrite", False))
    try:
        folder = _resolve_existing_output_folder(payload.get("folder"), base)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    zip_path = folder.parent / f"{folder.name}.zip"
    if zip_path.exists() and not overwrite:
        return JSONResponse({"error": f"zip exists: {zip_path}", "exists": True}, status_code=409)
    if zip_path.exists() and overwrite:
        policy = _path_policy_for(base)
        if not policy.is_generated_output(zip_path):
            return JSONResponse({"error": f"refusing to overwrite non-generated zip: {zip_path}"}, status_code=409)

    try:
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(folder):
                for name in files:
                    full = Path(root) / name
                    if full.is_symlink() or not full.is_file():
                        continue
                    rel = full.relative_to(folder)
                    try:
                        zf.write(full, rel.as_posix())
                    except Exception:
                        continue
    except Exception as exc:
        return JSONResponse({"error": f"zip failed: {exc}"}, status_code=500)

    _allow_path(zip_path)
    return JSONResponse({"zip_path": str(zip_path), "download_url": _download_url_for_path(zip_path, ctx=srv)})


@app.post("/api/verify_tokens")
def post_verify_tokens(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    sel = payload.get("selected_paths") or []
    ensure_summaries = bool(payload.get("ensure_summaries", True))
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    if not isinstance(sel, list) or not sel:
        return JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    files_abs, err = _resolve_input_file_values(sel, base, srv, field_name="selected_paths", empty_error="no valid files")
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "no valid files"}, status_code=400)

    cfg = srv.config.get_config()
    ui = _payload_to_ui_state(payload, cfg)
    try:
        ctx_data = _prepare_merge_context(
            ctx=srv,
            base=base,
            files_abs=files_abs,
            payload=payload,
            cfg=cfg,
            ensure_summaries=ensure_summaries,
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    total_est, content_only = srv.processor.merge_group_token_details(
        directory=str(base),
        files=ctx_data["files_for_merge"],
        include_file_tree=ui.output.include_tree,
        custom_start_label=ui.output.custom_start,
        custom_end_label=ui.output.custom_end,
        file_tree_start_label=ui.output.tree_start,
        file_tree_end_label=ui.output.tree_end,
        prompt_template=ui.summary.prompt_template,
        remove_empty=ui.filters.remove_empty,
        enumerate_lines=ui.filters.enumerate_lines,
        markdown_fences=ui.output.markdown_fences,
        markdown_toc=ui.output.markdown_toc,
        content_mode="code_only",
        summaries_by_file=None,
        summaries_short=ctx_data["summaries_short"],
        summaries_long=ctx_data["summaries_long"],
        content_mode_map=ctx_data["content_mode_map"],
        files_with_summary_errors=ctx_data["summary_errors"],
        fallback_to_code_on_summary_error=bool(cfg.get("fallback_to_code_on_summary_error", False)),
        strict_summary_required=ctx_data["strict_required"],
        virtual_blocks=ctx_data["virtual_blocks"],
        summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
        pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
    )
    tokens_total_exact = None
    use_exact_total = payload.get("exact_text_total")
    if use_exact_total is None or bool(use_exact_total):
        try:
            buf = StringIO()
            merge_basic_mod.merge_files(
                srv.processor,
                directory=str(base),
                files_to_merge=ctx_data["files_for_merge"],
                output_file=os.devnull,
                progress_callback=None,
                include_file_tree=ui.output.include_tree,
                include_summaries=False,
                custom_start_label=ui.output.custom_start,
                custom_end_label=ui.output.custom_end,
                file_tree_start_label=ui.output.tree_start,
                file_tree_end_label=ui.output.tree_end,
                create_json_manifest=False,
                summaries_by_file=None,
                summaries_short=ctx_data["summaries_short"],
                summaries_long=ctx_data["summaries_long"],
                strict_summary_required=ctx_data["strict_required"],
                content_mode="code_only",
                files_with_summary_errors=ctx_data["summary_errors"],
                prompt_template=ui.summary.prompt_template,
                output_stream=buf,
                remove_empty=ui.filters.remove_empty,
                enumerate_lines=ui.filters.enumerate_lines,
                markdown_fences=ui.output.markdown_fences,
                markdown_toc=ui.output.markdown_toc,
                format_summary=_format_summary,
                content_mode_map=ctx_data["content_mode_map"],
                fallback_to_code_on_summary_error=bool(
                    cfg.get("fallback_to_code_on_summary_error", False)
                ),
                virtual_blocks=ctx_data["virtual_blocks"],
                summarized_folder_rel_paths=ctx_data["summarized_folder_rel_paths"],
                pinned_file_rel_paths=ctx_data["pinned_file_rel_paths"],
            )
            tokens_total_exact = int(srv.processor.count_tokens(buf.getvalue()))
        except Exception:
            tokens_total_exact = None

    tokens_total = tokens_total_exact if tokens_total_exact is not None else int(total_est)
    resp: Dict[str, Any] = {
        "tokens_total": int(tokens_total),
        "tokens_content_only": int(content_only),
    }
    if tokens_total_exact is None:
        resp["tokens_total_estimated"] = int(total_est)
    else:
        resp["tokens_total_exact"] = int(tokens_total_exact)
    if ctx_data["summary_errors"]:
        resp["summary_files_with_errors"] = ctx_data["summary_errors"]
    return JSONResponse(resp)


# ----------------- Logs endpoint -----------------
def _tail_lines(path: Path, max_lines: int = 1000) -> list[str]:
    try:
        # Simple tail: read file and keep last N lines; sufficient for small logs
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            return lines[-max_lines:]
        return lines
    except Exception:
        return []


@app.get("/api/logs/summarizer")
def get_summarizer_log(tail: int = 1000) -> JSONResponse:
    log_path = LOG_DIR / "summarizer.log"
    lines = _tail_lines(log_path, max_lines=max(1, min(int(tail or 1000), 5000)))
    return JSONResponse({
        "path": str(log_path),
        "lines": lines,
        "count": len(lines),
    })


# ----------------- Job management + prune -----------------

PRUNE_CONFIG_KEYS: set[str] = {
    "prune_ranking_mode",
    "prune_engine",
    "prune_neighbors_mode",
    "prune_mode",
    "prune_dry_run",
    "prune_token_budget",
    "enable_embeddings",
    "retrieval_backend",
    "prune_goal_llm_enabled",
    "prune_goal_llm_span_select",
    "llm_neighbors_mode",
    "llm_prefilter_top_k",
    "llm_neighbor_max",
    "prune_neighbor_max",
    "prune_exclude_tests",
    "groq_strict_mode",
    "prune_downrank_noise",
    "prune_noise_dirs",
    "prune_top_k",
    "prune_keep_neighbors",
    "prune_max_repairs",
    "prune_span_to_file_threshold",
    "prune_keep_class_for_method",
    "prune_core_budget_tokens",
    "prune_repair_budget_tokens",
    "prune_repair_max_spans_per_module",
    "prune_repair_prefer_spans",
}

_PRUNE_CONFIG_INT_KEYS: set[str] = {
    "prune_token_budget",
    "llm_prefilter_top_k",
    "llm_neighbor_max",
    "prune_neighbor_max",
    "prune_top_k",
    "prune_max_repairs",
    "prune_span_to_file_threshold",
    "prune_core_budget_tokens",
    "prune_repair_budget_tokens",
    "prune_repair_max_spans_per_module",
}

_PRUNE_CONFIG_BOOL_KEYS: set[str] = {
    "prune_dry_run",
    "enable_embeddings",
    "prune_goal_llm_enabled",
    "prune_goal_llm_span_select",
    "prune_exclude_tests",
    "groq_strict_mode",
    "prune_downrank_noise",
    "prune_keep_neighbors",
    "prune_keep_class_for_method",
    "prune_repair_prefer_spans",
}

_PRUNE_CONFIG_LIST_STR_KEYS: set[str] = {"prune_noise_dirs"}


def _build_prune_run_config(base_cfg: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    run_cfg = dict(base_cfg or {})
    run_cfg["prune_execute_workspace"] = isinstance(payload, dict) and payload.get("prune_execute_workspace") is True
    if not isinstance(payload, dict):
        return run_cfg

    for key in PRUNE_CONFIG_KEYS:
        if key not in payload:
            continue
        val = payload.get(key)
        if val is None:
            continue

        if key in _PRUNE_CONFIG_INT_KEYS:
            default_val = _safe_int(run_cfg.get(key), 0)
            run_cfg[key] = max(0, _safe_int(val, default_val))
            continue

        if key in _PRUNE_CONFIG_BOOL_KEYS:
            default_val = _safe_bool(run_cfg.get(key), False)
            run_cfg[key] = _safe_bool(val, default_val)
            continue

        if key in _PRUNE_CONFIG_LIST_STR_KEYS:
            if isinstance(val, list):
                out: list[str] = []
                for item in val:
                    try:
                        s = str(item).strip()
                    except Exception:
                        continue
                    if s:
                        out.append(s)
                run_cfg[key] = out
            elif isinstance(val, str):
                run_cfg[key] = [s.strip() for s in val.split(",") if s.strip()]
            continue

        # default: string-ish settings
        if isinstance(val, str):
            run_cfg[key] = val
        else:
            try:
                run_cfg[key] = str(val)
            except Exception:
                pass

    return run_cfg


def _resolve_prune_output_path(base: Path, payload: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    raw = None
    for key in ("output_path", "output_file", "prune_output_path"):
        val = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(val, str) and val.strip():
            raw = val.strip()
            break
    if not raw:
        raw = cfg.get("prune_output_path", "")
    if not isinstance(raw, str) or not raw.strip():
        raw = str(base / "pruned_result.txt")
    policy = _path_policy_for(
        base,
        allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
    )
    resolved = policy.resolve_output_file(raw, default_name="pruned_result.txt")
    return str(resolved.path)


def _load_prune_by_goal():
    """Import codemerge.agent.core lazily so builds without the package still work."""
    try:
        import codemerge.agent.core as agent_core
    except ModuleNotFoundError as exc:  # agent package removed from build
        raise RuntimeError("Pruning feature is unavailable in this build.") from exc
    except Exception as exc:  # pragma: no cover - defensive logging path
        raise RuntimeError(f"Failed to load pruning feature: {exc}") from exc
    prune_fn = getattr(agent_core, "prune_by_goal", None)
    if not callable(prune_fn):
        raise RuntimeError("Pruning feature is unavailable in this build.")
    return prune_fn


def _run_prune_job(
    ctx: ServerContext,
    job_id: str,
    root: str,
    goal: str,
    cfg: Dict[str, Any],
    allowed_paths: list[str] | None,
    output_path: str,
) -> None:
    def _progress(msg: str) -> None:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job:
                job["progress"] = msg
    try:
        prune_fn = _load_prune_by_goal()
        # Choose output path; separate from merge output
        out_path = output_path
        plan = prune_fn(
            goal,
            root,
            out_path,
            config=cfg,
            progress=_progress,
            allowed_paths=allowed_paths,
        )
        _allow_path(out_path)
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {"output_path": out_path, "plan": plan}
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


def _run_summarize_job(ctx: ServerContext, job_id: str, root: str, files: list[str], content_mode: str, cfg: Dict[str, Any]) -> None:
    try:
        def _progress(done: int, total: int) -> None:
            pct = 0
            try:
                pct = int(round((done / total) * 100.0)) if total else 0
            except Exception:
                pct = 0
            _jobs_progress(ctx, job_id, f"Summarizing: {done}/{total} ({pct}%)", pct)

        _progress(0, len(files))
        summaries: Dict[str, Optional[str]] = {}
        errors: List[str] = []
        root_abs = _canonicalize_path(root) if root else ""
        mode = _normalize_content_mode(content_mode, default="code_plus_short")
        if mode in _SUMMARY_PROVIDER_MODES:
            kind = "short" if mode in {
                "code_plus_short",
                "short_only",
                "short_plus_skeleton",
                "code_plus_short_plus_skeleton",
            } else "long"
            cfg_limited = _limit_groq_workers(cfg)
            prompt_version = int(cfg_limited.get("summary_prompt_version", 7) or 7)
            key = _file_summary_job_key(kind, root_abs, files, prompt_version)
            summaries, errors = ctx.summarization_manager.run(
                key,
                ensure_summaries,
                files,
                kind,
                ctx.processor,
                cfg_limited,
                _progress,
                force=False,
                root=root_abs,
            )
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {
                    "summarized": len(summaries),
                    "errors": errors,
                    "summaries": summaries,
                }
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
        # Store for later merge
        ctx.summaries = {k: v for k, v in summaries.items()}
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


def _run_summarize_folder_job(
    ctx: ServerContext,
    job_id: str,
    folder_abs: str,
    mode: str | None,
    cfg: Dict[str, Any],
    snapshot: JobInputSnapshot | None = None,
) -> None:
    try:
        cfg_limited = _limit_groq_workers(cfg)
        mode = _summary_mode_or_default(mode, default="full")
        if snapshot is None:
            ctx.core.configure(cfg_limited)
            scan_ctx = ctx.core.scan_ctx
        else:
            scan_ctx = snapshot.scan_ctx
        if scan_ctx is None:
            raise RuntimeError("scan context missing; run /api/scan first")
        root_abs = str(scan_ctx.root)
        aggregate_scan_files = _scan_files_for_aggregate(
            ctx=ctx,
            root_abs=root_abs,
            cfg=cfg_limited,
            payload=None,
            scan_ctx=scan_ctx,
        )
        prompt_version = int(cfg_limited.get("summary_prompt_version", 7) or 7)
        model_name, runtime_fingerprint = _summary_job_runtime_contract(
            target_kind="folder",
            mode=mode,
            cfg=cfg_limited,
        )
        key = _folder_summary_job_key_with_scope(
            folder_abs,
            root_abs,
            prompt_version,
            mode,
            model_name=model_name,
            runtime_fingerprint=runtime_fingerprint,
            paths=[str(meta.abs_path) for meta in aggregate_scan_files],
        )
        def _progress(stage: str, done: int | None, total: int | None) -> None:
            msg = stage
            pct = None
            if total:
                msg = f"{stage} {done}/{total}"
                try:
                    pct = int(round((float(done or 0) / float(total)) * 100))
                except Exception:
                    pct = None
            _jobs_progress(ctx, job_id, msg, pct)
        obj = ctx.summarization_manager.run(
            key,
            ensure_folder_summary,
            folder_abs=folder_abs,
            root_abs=root_abs,
            scan_files=list(aggregate_scan_files),
            file_processor=ctx.processor,
            app_config=cfg_limited,
            mode=mode,
            progress_cb=_progress,
            scope_source="aggregate_scan",
        )
        text = render_folder_summary(folder_abs, obj, mode=mode, profile="explorer")
        try:
            token_count = int(ctx.processor.count_tokens(text))
        except Exception:
            token_count = 0
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {
                    "summary_text": text,
                    "token_count": token_count,
                }
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


def _run_summarize_repo_job(
    ctx: ServerContext,
    job_id: str,
    mode: str | None,
    cfg: Dict[str, Any],
    snapshot: JobInputSnapshot | None = None,
) -> None:
    try:
        cfg_limited = _limit_groq_workers(cfg)
        mode = _summary_mode_or_default(mode, default="full")
        if snapshot is None:
            ctx.core.configure(cfg_limited)
            scan_ctx = ctx.core.scan_ctx
        else:
            scan_ctx = snapshot.scan_ctx
        if scan_ctx is None:
            raise RuntimeError("scan context missing; run /api/scan first")
        root_abs = str(scan_ctx.root)
        aggregate_scan_files = _scan_files_for_aggregate(
            ctx=ctx,
            root_abs=root_abs,
            cfg=cfg_limited,
            payload=None,
            scan_ctx=scan_ctx,
        )
        if not aggregate_scan_files:
            raise RuntimeError("Repository summary scope is empty after exclusions.")
        prompt_version = int(cfg_limited.get("summary_prompt_version", 7) or 7)
        model_name, runtime_fingerprint = _summary_job_runtime_contract(
            target_kind="repo",
            mode=mode,
            cfg=cfg_limited,
        )
        key = _repo_summary_job_key(
            root_abs,
            prompt_version,
            mode,
            model_name=model_name,
            runtime_fingerprint=runtime_fingerprint,
            paths=[str(meta.abs_path) for meta in aggregate_scan_files],
        )
        def _progress(stage: str, done: int | None, total: int | None) -> None:
            msg = stage
            pct = None
            if total:
                msg = f"{stage} {done}/{total}"
                try:
                    pct = int(round((float(done or 0) / float(total)) * 100))
                except Exception:
                    pct = None
            _jobs_progress(ctx, job_id, msg, pct)
        obj = ctx.summarization_manager.run(
            key,
            ensure_repo_summary,
            root_abs=root_abs,
            scan_files=list(aggregate_scan_files),
            file_processor=ctx.processor,
            app_config=cfg_limited,
            mode=mode,
            progress_cb=_progress,
            scope_source="aggregate_scan",
        )
        text = render_repo_summary(obj, mode=mode, profile="explorer")
        try:
            token_count = int(ctx.processor.count_tokens(text))
        except Exception:
            token_count = 0
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {
                    "summary_text": text,
                    "token_count": token_count,
                }
                job["progress"] = "done"
                job["progress_pct"] = 100
                job["completed_at"] = _utc_now_iso()
    except Exception as exc:
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = _utc_now_iso()


def _jobs_progress(ctx: ServerContext, job_id: str, msg: str, pct: float | None = None) -> None:
    with ctx.jobs_lock:
        job = ctx.jobs.get(job_id)
        if job is not None:
            job["progress"] = msg
            if pct is not None:
                try:
                    job["progress_pct"] = int(round(float(pct)))
                except Exception:
                    pass


def _job_output_paths(job: Dict[str, Any]) -> tuple[str, ...]:
    paths: list[str] = []

    def add(raw: object) -> None:
        if isinstance(raw, str) and raw and raw not in paths:
            paths.append(raw)

    result = job.get("result")
    if isinstance(result, dict):
        for key in ("output_path", "output_file", "output_folder", "zip_path"):
            add(result.get(key))
        for key in ("output_paths", "output_files", "output_folders"):
            raw_list = result.get(key)
            if isinstance(raw_list, list):
                for item in raw_list:
                    add(item)
    for key in ("output_path", "output_file", "output_folder", "zip_path"):
        add(job.get(key))
    return tuple(paths)


def _job_snapshot(job: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(job.get("id") or "")
    status = str(job.get("status") or JobStatus.RUNNING.value)
    job_type = str(job.get("type") or JobType.UNKNOWN.value)
    progress = str(job.get("progress") or status)
    try:
        progress_pct = int(round(float(job.get("progress_pct", 0) or 0)))
    except Exception:
        progress_pct = 0
    progress_pct = max(0, min(progress_pct, 100))
    snapshot = JobSnapshot(
        id=job_id,
        type=job_type,
        status=status,
        progress=progress,
        progress_pct=progress_pct,
        created_at=str(job.get("created_at")) if job.get("created_at") is not None else None,
        completed_at=str(job.get("completed_at")) if job.get("completed_at") is not None else None,
        result=job.get("result"),
        error=str(job.get("error")) if job.get("error") is not None else None,
        output_paths=_job_output_paths(job),
        cancellable=bool(job.get("cancellable", False)),
        cancel_supported=bool(job.get("cancel_supported", job.get("cancellable", False))),
    )
    out = dict(job)
    out.update(
        {
            "id": snapshot.id,
            "type": snapshot.type,
            "status": snapshot.status,
            "progress": snapshot.progress,
            "progress_pct": snapshot.progress_pct,
            "created_at": snapshot.created_at,
            "completed_at": snapshot.completed_at,
            "result": snapshot.result,
            "error": snapshot.error,
            "output_paths": list(snapshot.output_paths),
            "cancellable": snapshot.cancellable,
            "cancel_supported": snapshot.cancel_supported,
        }
    )
    return out


@app.post("/api/prune")
def post_prune(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    goal = payload.get("goal")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    if not isinstance(goal, str) or not goal.strip():
        return JSONResponse({"error": "goal must be a non-empty string"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)

    if importlib.util.find_spec("codemerge.agent.core") is None:
        return JSONResponse({"error": "Pruning feature is unavailable in this build."}, status_code=503)

    # Build config for pruning based on AppConfig with overrides from payload
    base_cfg = srv.config.get_config()
    cfg = _build_prune_run_config(base_cfg, payload)
    try:
        output_path = _resolve_prune_output_path(base, payload, base_cfg)
    except PathPolicyError as exc:
        return _path_error_response(exc)
    # Persist chosen output path for next run
    try:
        if isinstance(payload.get("output_path"), str) or isinstance(payload.get("output_file"), str):
            srv.config.update_config({"prune_output_path": output_path})
    except Exception:
        pass

    allowed_paths = None
    sel = payload.get("selected_paths")
    if isinstance(sel, list) and sel:
        allowed_paths, err = _resolve_input_file_values(sel, base, srv, field_name="selected_paths")
        if err is not None or not allowed_paths:
            return err or JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    elif isinstance(payload.get("visible_paths"), list):
        # WYSIWYG scope: trust the frontend's current visible list (e.g., after
        # session hides), and do not reconstruct via filters.
        raw_visible = payload.get("visible_paths")
        srv.core.configure(cfg)
        resolved_visible, err = _resolve_input_file_values(raw_visible, base, srv, field_name="visible_paths")
        if err is not None or not resolved_visible:
            return err or JSONResponse({"error": "No visible files provided for pruning. Adjust filters and rescan."}, status_code=400)
        out_paths: list[str] = []
        for abs_path in resolved_visible:
            try:
                if srv.core.get_mode(abs_path) is FileMode.EXCLUDE:
                    continue
            except Exception:
                pass
            out_paths.append(abs_path)

        allowed_paths = out_paths
        if not allowed_paths:
            return JSONResponse(
                {"error": "No visible files provided for pruning. Adjust filters and rescan."},
                status_code=400,
            )
    else:
        # Safety: if nothing is explicitly selected, restrict pruning to the
        # same "visible files" universe as /api/scan (i.e., honor filters).
        scan_payload = dict(payload) if isinstance(payload, dict) else {}
        scan_payload["path"] = str(base)
        if "include_all" not in scan_payload:
            # JS UI scan forces respecting configured file types only.
            scan_payload["include_all"] = False
        if not isinstance(scan_payload.get("filters"), dict):
            scan_payload["filters"] = cfg.get("last_filters", {}) if isinstance(cfg.get("last_filters", {}), dict) else {}
        try:
            _scan_ctx, _ui, visible, _dup_set, warnings, _pattern_hits = _scan_visible(base, scan_payload, cfg)
            srv.core.configure(cfg)
            allowed_paths = []
            for m in visible:
                abs_path = str(m.abs_path)
                try:
                    if srv.core.get_mode(abs_path) is FileMode.EXCLUDE:
                        continue
                except Exception:
                    pass
                allowed_paths.append(abs_path)
            if not allowed_paths:
                detail = (" (" + "; ".join(warnings) + ")") if warnings else ""
                return JSONResponse(
                    {"error": "No files visible for pruning. Adjust filters and rescan." + detail},
                    status_code=400,
                )
        except Exception as exc:
            return JSONResponse({"error": f"Failed to compute prune scope: {exc}"}, status_code=500)

    prune_risks = _prune_provider_risks(cfg)
    preflight = _provider_preflight_decision(
        srv,
        "prune",
        cfg=cfg,
        root=str(base),
        target=str(base),
        paths=allowed_paths or [],
        requires_confirmation=bool(prune_risks),
        risks=prune_risks,
        estimated_provider_calls=max(1, len(prune_risks)) if prune_risks else 0,
    )
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate

    job_id, err = srv.job_service.submit(
        JobType.PRUNE,
        _run_prune_job,
        (srv, str(base), goal, cfg, allowed_paths, output_path),
        metadata=_provider_job_metadata(preflight),
    )
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id, "output_path": output_path})


@app.get("/api/jobs", response_model=JobsListResponse)
def list_jobs(status: str | None = None, type: str | None = None, limit: int = 100) -> JSONResponse:
    srv = _require_ctx()
    try:
        max_items = max(1, min(int(limit or 100), 500))
    except Exception:
        max_items = 100
    status_filter = str(status).strip().lower() if isinstance(status, str) and status.strip() else None
    type_filter = str(type).strip().lower() if isinstance(type, str) and type.strip() else None
    with srv.jobs_lock:
        srv.job_service.cleanup_locked()
        snapshots = [_job_snapshot(job) for job in srv.jobs.values()]
    if status_filter:
        snapshots = [job for job in snapshots if str(job.get("status", "")).lower() == status_filter]
    if type_filter:
        snapshots = [job for job in snapshots if str(job.get("type", "")).lower() == type_filter]
    snapshots.sort(key=lambda job: str(job.get("created_at") or ""), reverse=True)
    snapshots = snapshots[:max_items]
    return JSONResponse({"jobs": snapshots, "count": len(snapshots)})


@app.get("/api/jobs/{job_id}", response_model=JobSnapshotResponse)
def get_job(job_id: str) -> JSONResponse:
    srv = _require_ctx()
    with srv.jobs_lock:
        srv.job_service.cleanup_locked()
        job = srv.jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        return JSONResponse(_job_snapshot(job))


@app.post("/api/jobs/{job_id}/cancel", response_model=JobCancelResponse)
def cancel_job(job_id: str) -> JSONResponse:
    srv = _require_ctx()
    job, message, status_code = srv.job_service.cancel(job_id)
    if job is None:
        return JSONResponse(
            {"ok": False, "job_id": job_id, "status": "unknown", "cancel_supported": False, "message": message},
            status_code=status_code,
        )
    snapshot = _job_snapshot(job)
    return JSONResponse(
        {
            "ok": status_code == 200,
            "job_id": snapshot["id"],
            "status": snapshot["status"],
            "cancel_supported": bool(snapshot.get("cancel_supported", False)),
            "message": message,
        },
        status_code=status_code,
    )


@app.post("/api/summarize")
def post_summarize(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    sel = payload.get("selected_paths")
    try:
        content_mode = _normalize_content_mode(
            payload.get("content_mode", "code_plus_short"),
            default="code_plus_short",
            strict=True,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    if not isinstance(sel, list) or not sel:
        return JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    base = Path(path)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)
    _ensure_config_for_path(path, ctx=srv)
    files_abs, err = _resolve_input_file_values(sel, base, srv, field_name="selected_paths", empty_error="no valid files")
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "no valid files"}, status_code=400)
    cfg = srv.config.get_config().copy()
    preflight = _summary_provider_preflight(
        srv,
        "summarize",
        cfg=cfg,
        root=str(base),
        target=str(base),
        paths=files_abs,
        content_mode=content_mode,
    )
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate

    job_id, err = srv.job_service.submit(
        JobType.SUMMARIZE,
        _run_summarize_job,
        (srv, str(base), files_abs, content_mode, cfg),
        metadata=_provider_job_metadata(preflight),
    )
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id})


@app.post("/api/summarize_folder")
def post_summarize_folder(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    folder_path = payload.get("path")
    root = srv.config.get_config().get("last_directory")
    if not isinstance(folder_path, str) or not folder_path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    if not isinstance(root, str) or not root:
        return JSONResponse({"error": "root path is missing; run scan first"}, status_code=400)
    base = Path(root)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {root}"}, status_code=400)
    folder = Path(folder_path)
    if not folder.is_absolute():
        folder = base / folder
    try:
        folder = folder.resolve()
    except Exception:
        pass
    try:
        _ = folder.relative_to(base.resolve())
    except Exception:
        return JSONResponse({"error": "access denied"}, status_code=403)
    if not folder.exists() or not folder.is_dir():
        return JSONResponse({"error": f"path is not a directory: {folder}"}, status_code=400)

    ctx = srv.core.scan_ctx
    if ctx is None:
        return JSONResponse({"error": "no scan context; run /api/scan first"}, status_code=409)
    try:
        ctx_root = Path(getattr(ctx, "root")).resolve()
        if ctx_root != base.resolve():
            return JSONResponse(
                {"error": "scan context root does not match path; run /api/scan", "needs_scan": True},
                status_code=409,
            )
    except Exception:
        return JSONResponse({"error": "invalid scan context; run /api/scan"}, status_code=409)
    try:
        rel_from_root = os.path.relpath(folder, base.resolve())
    except Exception:
        rel_from_root = str(folder.name)
    scan_root = Path(ctx.root)
    folder_for_scan = scan_root / rel_from_root
    raw_mode = payload.get("mode") if isinstance(payload, dict) else None
    try:
        mode = _summary_mode_or_default(raw_mode, default="full")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    cfg = srv.config.get_config()
    scan_paths = [str(meta.abs_path) for meta in getattr(ctx, "files", [])]
    preflight = _summary_provider_preflight(
        srv,
        "summarize_folder",
        cfg=cfg,
        root=str(base),
        target=str(folder_for_scan),
        paths=scan_paths,
        mode=mode,
    )
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate

    snapshot = _capture_job_input_snapshot(srv, base)
    job_id, err = srv.job_service.submit(
        JobType.SUMMARIZE_FOLDER,
        _run_summarize_folder_job,
        (srv, str(folder_for_scan), mode, copy.deepcopy(snapshot.config), snapshot),
        metadata={**_job_snapshot_metadata(snapshot), **_provider_job_metadata(preflight)},
    )
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id})


@app.post("/api/summarize_repo")
def post_summarize_repo(payload: Dict[str, Any] = Body(None)) -> JSONResponse:
    srv = _require_ctx()
    cfg = srv.config.get_config()
    root = cfg.get("last_directory")
    if not isinstance(root, str) or not root:
        return JSONResponse({"error": "root path is missing; run scan first"}, status_code=400)
    base = Path(root)
    if not base.exists() or not base.is_dir():
        return JSONResponse({"error": f"path is not a directory: {root}"}, status_code=400)
    ctx = srv.core.scan_ctx
    if ctx is None:
        return JSONResponse({"error": "no scan context; run /api/scan first"}, status_code=409)
    try:
        ctx_root = Path(getattr(ctx, "root")).resolve()
        if ctx_root != base.resolve():
            return JSONResponse(
                {"error": "scan context root does not match path; run /api/scan", "needs_scan": True},
                status_code=409,
            )
    except Exception:
        return JSONResponse({"error": "invalid scan context; run /api/scan"}, status_code=409)
    raw_mode = payload.get("mode") if isinstance(payload, dict) else None
    try:
        mode = _summary_mode_or_default(raw_mode, default="full")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    scan_paths = [str(meta.abs_path) for meta in getattr(ctx, "files", [])]
    preflight = _summary_provider_preflight(
        srv,
        "summarize_repo",
        cfg=cfg,
        root=str(base),
        target=str(base),
        paths=scan_paths,
        mode=mode,
    )
    gate = _provider_confirmation_response(preflight, payload if isinstance(payload, dict) else {})
    if gate is not None:
        return gate

    snapshot = _capture_job_input_snapshot(srv, base)
    job_id, err = srv.job_service.submit(
        JobType.SUMMARIZE_REPO,
        _run_summarize_repo_job,
        (srv, mode, copy.deepcopy(snapshot.config), snapshot),
        metadata={**_job_snapshot_metadata(snapshot), **_provider_job_metadata(preflight)},
    )
    if err is not None or job_id is None:
        return JSONResponse({"error": err or "job could not be created"}, status_code=429)
    return JSONResponse({"job_id": job_id})


@app.post("/api/full_coverage_proof")
def post_full_coverage_proof(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    cfg = srv.config.get_config()
    root = cfg.get("last_directory")
    if not isinstance(root, str) or not root:
        return JSONResponse({"error": "root path is missing; run scan first"}, status_code=400)
    ctx = srv.core.scan_ctx
    if ctx is None:
        return JSONResponse({"error": "no scan context; run /api/scan first"}, status_code=409)
    target_kind = payload.get("target_kind") if isinstance(payload, dict) else None
    target_kind = str(target_kind or "").strip().lower()
    if target_kind not in {"folder", "repo"}:
        return JSONResponse({"error": "target_kind must be 'folder' or 'repo'"}, status_code=400)
    target_path = payload.get("target_path") if isinstance(payload, dict) else None
    if target_kind == "repo":
        target_path = root
    if not isinstance(target_path, str) or not target_path:
        return JSONResponse({"error": "target_path is required"}, status_code=400)
    base = Path(root)
    path = Path(target_path)
    if not path.is_absolute():
        path = base / path
    try:
        path = path.resolve()
    except Exception:
        pass
    try:
        path.relative_to(base.resolve())
    except Exception:
        return JSONResponse({"error": "access denied"}, status_code=403)
    manifest_hash = payload.get("manifest_hash") if isinstance(payload, dict) else None
    if not manifest_hash:
        try:
            aggregate_scan_files = _scan_files_for_aggregate(
                ctx=srv,
                root_abs=root,
                cfg=cfg,
                payload=None,
            )
            scoped_metas, _folder_prefix = filter_folder_metas(
                str(path) if target_kind == "folder" else root,
                root,
                aggregate_scan_files,
            )
            in_scope, _skipped, _errors = inventory_in_scope(
                metas=scoped_metas,
                file_processor=srv.processor,
            )
            _rel_paths, manifest_hash = build_manifest(in_scope)
        except Exception:
            manifest_hash = None
    proof = get_full_coverage_proof(
        target_kind=target_kind,
        target_path=str(path),
        app_config=cfg,
        manifest_hash=manifest_hash,
    )
    if proof is None:
        return JSONResponse({"error": "coverage proof not found"}, status_code=404)
    return JSONResponse({"coverage": proof})


@app.post("/api/list_children")
def post_list_children(payload: Dict[str, Any] = Body(None)) -> JSONResponse:
    srv = _require_ctx()
    ctx = srv.core.scan_ctx
    if ctx is None:
        return JSONResponse({"error": "no scan context; run /api/scan first"}, status_code=409)
    root_abs = str(ctx.root)
    raw_path = payload.get("path") if isinstance(payload, dict) else None
    if raw_path is None:
        raw_path = root_abs
    if not isinstance(raw_path, str) or not raw_path:
        return JSONResponse({"error": "path is required"}, status_code=400)

    base = Path(root_abs)
    path = Path(raw_path)
    if not path.is_absolute():
        path = base / path
    try:
        path = path.resolve()
    except Exception:
        pass
    try:
        path.relative_to(base.resolve())
    except Exception:
        return JSONResponse({"error": "access denied"}, status_code=403)
    if not path.exists() or not path.is_dir():
        return JSONResponse({"error": f"path is not a directory: {path}"}, status_code=400)

    children = _list_children_from_scan(ctx, str(path))
    return JSONResponse({"root": root_abs, "path": str(path), "children": children})


# ----------------- Xcode targets -----------------
@app.get("/api/xcode_targets")
def get_xcode_targets(path: str) -> JSONResponse:
    srv = _require_ctx()
    base, err = _resolve_current_root_folder(path, srv, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    try:
        mapping = srv.processor.xcode_target_sources(str(base))
        return JSONResponse({"targets": sorted(mapping.keys())})
    except Exception as exc:
        return JSONResponse({"targets": [], "error": str(exc)})


# ----------------- Watch fingerprint -----------------
@app.get("/api/watch/fingerprint")
def get_fingerprint(path: str, sample_limit: int = 3000, use_git: bool = False, include_untracked: bool = False) -> JSONResponse:
    srv = _require_ctx()
    base, err = _resolve_current_root_folder(path, srv, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    cfg = srv.config.get_config()
    ui = _payload_to_ui_state(
        {
            "path": str(base),
            "use_git_files": bool(use_git),
            "git_include_untracked": bool(include_untracked),
        },
        cfg,
    )

    excluded_dirs = _compute_excluded_dirs(cfg, None, processor=srv.processor)
    excluded_files = _effective_excluded_files(cfg)

    count, max_mtime = watch_service.compute_fingerprint(
        srv.processor,
        ui,
        int(sample_limit or 3000),
        excluded_dirs=excluded_dirs,
        excluded_files=excluded_files,
    )
    return JSONResponse({"count": int(count), "max_mtime": float(max_mtime)})


@app.get("/api/watch/stream")
async def watch_stream(
    request: Request,
    path: str,
    interval_sec: float = 3.0,
    sample_limit: int = 3000,
    use_git: bool = False,
    include_untracked: bool = False,
) -> StreamingResponse:
    """Server-sent events stream of fingerprint updates.

    This reduces client polling overhead for watch mode in the JS UI.
    """
    srv = _require_ctx()
    base, err = _resolve_current_root_folder(path, srv, allow_initial_root=True)
    if err is not None or base is None:
        status = getattr(err, "status_code", 400) if err is not None else 400
        detail = "access denied" if status == 403 else "invalid path"
        if status == 409:
            detail = "root path is missing; run scan first"
        raise HTTPException(status_code=status, detail=detail)

    try:
        interval = max(0.5, float(interval_sec or 3.0))
    except Exception:
        interval = 3.0
    limit = max(100, int(sample_limit or 3000))

    async def _gen():
        last_key: str | None = None
        while True:
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                pass

            cfg = srv.config.get_config()
            ui = _payload_to_ui_state(
                {
                    "path": str(base),
                    "use_git_files": bool(use_git),
                    "git_include_untracked": bool(include_untracked),
                },
                cfg,
            )
            excluded_dirs = _compute_excluded_dirs(cfg, None, processor=srv.processor)
            excluded_files = _effective_excluded_files(cfg)
            try:
                count, max_mtime = await asyncio.to_thread(
                    watch_service.compute_fingerprint,
                    srv.processor,
                    ui,
                    limit,
                    excluded_dirs=excluded_dirs,
                    excluded_files=excluded_files,
                )
            except Exception:
                count, max_mtime = (0, 0.0)

            payload = {"count": int(count), "max_mtime": float(max_mtime)}
            key = f"{payload['count']}:{payload['max_mtime']}"
            if last_key is None or key != last_key:
                last_key = key
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                # keepalive to prevent proxies from closing idle streams
                yield ":keepalive\n\n"

            try:
                await asyncio.sleep(interval)
            except Exception:
                break

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/api/watch/tick")
def post_watch_tick(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    sel = payload.get("selected_paths")
    only_summary = bool(payload.get("only_summary", True))
    default_override = payload.get("default_mode")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    if not isinstance(sel, list) or not sel:
        return JSONResponse({"error": "selected_paths must be a non-empty array"}, status_code=400)
    base, err = _resolve_current_root_folder(path, srv)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)

    files_abs, err = _resolve_input_file_values(sel, base, srv, field_name="selected_paths", empty_error="no valid files")
    if err is not None or not files_abs:
        return err or JSONResponse({"error": "no valid files"}, status_code=400)

    cfg = srv.config.get_config()
    srv.core.configure(cfg)
    content_override = None
    if default_override is not None:
        content_override = _normalize_content_mode(default_override, default="code_only")
    plan = srv.core.build_plan(files_abs, default_override=content_override)
    if only_summary:
        plan = {
            p: m
            for p, m in plan.items()
            if m
            in {
                FileMode.SUMMARY_SHORT,
                FileMode.SUMMARY_LONG,
                FileMode.CODE_PLUS_SHORT,
                FileMode.CODE_PLUS_LONG,
                FileMode.SUMMARY_SHORT_PLUS_SKELETON,
                FileMode.SUMMARY_LONG_PLUS_SKELETON,
                FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
                FileMode.CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON,
            }
        }
    if not plan:
        return JSONResponse({"ok": True, "metrics": {"requested_short": 0, "requested_long": 0, "ensured": 0, "errors": 0}})
    preflight = _summary_provider_preflight(
        srv,
        "watch_tick",
        cfg=cfg,
        root=str(base),
        target=str(base),
        paths=list(plan.keys()),
        mode="summary",
    )
    gate = _provider_confirmation_response(preflight, payload)
    if gate is not None:
        return gate

    try:
        cfg_limited = _limit_groq_workers(cfg)
        prompt_version = int(cfg_limited.get("summary_prompt_version", 7) or 7)
        key = _watch_summary_job_key(str(base), plan, prompt_version)
        result = srv.summarization_manager.run(
            key,
            srv.processor.watch_tick,
            str(base),
            plan,
            app_config=cfg_limited,
        )
    except Exception as exc:
        logger.exception("watch_tick failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
    requested_paths = result.get("requested_paths", []) if isinstance(result, dict) else []
    error_paths = result.get("error_paths", []) if isinstance(result, dict) else []
    return JSONResponse({"ok": True, "metrics": metrics, "requested_paths": requested_paths, "error_paths": error_paths})


# ----------------- File preview -----------------
@app.post("/api/file/preview", response_model=FilePreviewResponse)
def post_file_preview(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("file_path")
    max_kb = int(payload.get("max_kb", 256) or 256)
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "file_path required"}, status_code=400)
    p = Path(path).resolve()
    root = _current_project_root(srv)
    if root is None:
        return JSONResponse({"error": "root path is missing; run scan first"}, status_code=409)
    policy = _path_policy_for(root, ctx=srv)
    if not policy.can_download(p):
        return JSONResponse({"error": "access denied"}, status_code=403)
    try:
        if max_kb <= 0:
            max_kb = 256
        max_bytes = max_kb * 1024
        try:
            size_bytes = p.stat().st_size
        except Exception:
            size_bytes = None

        with p.open("rb") as f:
            b = f.read(max_bytes + 1)
        truncated = len(b) > max_bytes
        if truncated:
            b = b[:max_bytes]
        try:
            txt = b.decode("utf-8", errors="replace")
        except Exception:
            txt = ""
        shown_kb = int((len(b) + 1023) // 1024)
        return JSONResponse(
            {
                "text": txt,
                "file_path": str(p),
                "size_bytes": size_bytes,
                "max_kb": max_kb,
                "shown_kb": shown_kb,
                "truncated": truncated,
            }
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/open")
def post_open(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    base = Path(srv.config.get_config().get("last_directory", "") or ".")
    policy = _path_policy_for(base, ctx=srv)
    paths = payload.get("paths")
    if isinstance(paths, list) and paths:
        ok: list[str] = []
        err: list[str] = []
        for p in paths:
            try:
                pr = Path(p).resolve()
                if not policy.can_open(pr):
                    raise RuntimeError("access denied")
                open_file_in_default_app(str(pr))
                ok.append(str(pr))
            except Exception as exc:
                err.append(f"{p}: {exc}")
        return JSONResponse({"opened": ok, "errors": err})
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path(s) required"}, status_code=400)
    try:
        pr = Path(path).resolve()
        if not policy.can_open(pr):
            return JSONResponse({"opened": [], "errors": ["access denied"]}, status_code=403)
        open_file_in_default_app(str(pr))
        return JSONResponse({"opened": [str(pr)], "errors": []})
    except Exception as exc:
        return JSONResponse({"opened": [], "errors": [str(exc)]}, status_code=500)


# ----------------- Settings editors -----------------
@app.get("/api/settings")
def get_settings(path: str | None = None) -> JSONResponse:
    ctx = _require_ctx()
    if isinstance(path, str) and path:
        base, err = _resolve_current_root_folder(path, ctx, allow_initial_root=True)
        if err is not None or base is None:
            return err or JSONResponse({"error": "invalid path"}, status_code=400)
        _ensure_config_for_path(str(base), ctx=ctx)
    else:
        _ensure_config_for_path(ctx.config.get_config().get("last_directory"), ctx=ctx)
    cfg = ctx.config.get_config()
    excluded_dirs_raw = cfg.get("excluded_dirs") if "excluded_dirs" in cfg else None
    if excluded_dirs_raw is None:
        try:
            excluded_dirs_out = sorted(getattr(ctx.processor, "default_excluded_dirs", set()))
        except Exception:
            excluded_dirs_out = []
    elif isinstance(excluded_dirs_raw, (list, set, tuple)):
        excluded_dirs_out = list(excluded_dirs_raw)
    else:
        try:
            excluded_dirs_out = sorted(getattr(ctx.processor, "default_excluded_dirs", set()))
        except Exception:
            excluded_dirs_out = []
    return JSONResponse({
        "always_include_docs_list": cfg.get("always_include_docs_list", []),
        "custom_ext_map": cfg.get("custom_ext_map", {}),
        "excluded_dirs": excluded_dirs_out,
        "excluded_files": cfg.get("excluded_files", []),
        "custom_excluded": cfg.get("custom_excluded", []),
        # Advanced Groq + retrieval controls
        "groq_use_batch": cfg.get("groq_use_batch", False),
        "batch_progress": cfg.get("batch_progress", False),
        "groq_strict_mode": cfg.get("groq_strict_mode", True),
        "groq_max_workers": cfg.get("groq_max_workers", 0),
        "summary_token_limit": cfg.get("summary_token_limit", 0),
        "file_summary_chunk_tokens": cfg.get("file_summary_chunk_tokens", 0),
        "file_summary_max_output_tokens": cfg.get("file_summary_max_output_tokens", 0),
        "file_summary_unifier_max_output_tokens": cfg.get("file_summary_unifier_max_output_tokens", 0),
        "aggregate_prompt_budget_tokens": cfg.get("aggregate_prompt_budget_tokens", 0),
        "aggregate_summary_max_output_tokens": cfg.get("aggregate_summary_max_output_tokens", 0),
        "aggregate_selector_max_output_tokens": cfg.get("aggregate_selector_max_output_tokens", 0),
        "aggregate_critic_max_output_tokens": cfg.get("aggregate_critic_max_output_tokens", 0),
        "summary_default_mode": cfg.get("summary_default_mode", "CODE"),
        "auto_refresh_summaries": cfg.get("auto_refresh_summaries", True),
        "summarize_vendored": cfg.get("summarize_vendored", False),
        "fallback_to_code_on_summary_error": cfg.get("fallback_to_code_on_summary_error", False),
        "summary_change_strategy": cfg.get("summary_change_strategy", "mtime_size"),
        "summary_prompt_version": cfg.get("summary_prompt_version", 7),
        "groq_request_timeout_seconds": cfg.get("groq_request_timeout_seconds", 120.0),
        "groq_summary_retry_budget": cfg.get("groq_summary_retry_budget", 2),
        "strict_summary_required": cfg.get("strict_summary_required", False),
        "embedding_provider": cfg.get("embedding_provider", "groq"),
        "retrieval_backend": cfg.get("retrieval_backend", "embeddings"),
        # Merge naming and footer display
        "use_folder_name": cfg.get("use_folder_name", False),
        "append_datetime": cfg.get("append_datetime", False),
        "footer_final_only": cfg.get("footer_final_only", False),
        # UI preferences
        "ui_font_size": cfg.get("ui_font_size", 10),
        "ui_high_contrast": cfg.get("ui_high_contrast", False),
        "ui_compact_mode": cfg.get("ui_compact_mode", False),
        "show_totals_columns": cfg.get("show_totals_columns", False),
        "show_density_column": cfg.get("show_density_column", False),
        "show_bytes_footer": cfg.get("show_bytes_footer", True),
        "ui_watch_debounce_ms": cfg.get("ui_watch_debounce_ms", 800),
        "watch_sample_limit": cfg.get("watch_sample_limit", 3000),
        # Output + merge defaults
        "include_file_tree": cfg.get("include_file_tree", True),
        "include_json_manifest": cfg.get("include_json_manifest", False),
        "output_markdown_fences": cfg.get("output_markdown_fences", False),
        "output_markdown_toc": cfg.get("output_markdown_toc", False),
        "remove_empty": cfg.get("remove_empty", False),
        "enumerate_lines": cfg.get("enumerate_lines", False),
        "apply_token_filters_to_transforms": cfg.get("apply_token_filters_to_transforms", False),
        "custom_start_label": cfg.get("custom_start_label", "======= START OF FILE: {file_display} ======="),
        "custom_end_label": cfg.get("custom_end_label", "======= END OF FILE: {file_display} ======="),
        "custom_tree_start_label": cfg.get("custom_tree_start_label", "======= FILE TREE ======="),
        "custom_tree_end_label": cfg.get("custom_tree_end_label", "======= END OF FILE TREE ======="),
        "content_mode": cfg.get("content_mode", "code_only"),
        "prompt_template": cfg.get("prompt_template", ""),
        "structure_mode": cfg.get("structure_mode", "single_file"),
        "split_into_n": cfg.get("split_into_n", 5),
        "token_limit": cfg.get("token_limit", 1_048_576),
    })


@app.put("/api/settings")
def put_settings(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        path = srv.config.get_config().get("last_directory")
    if isinstance(path, str) and path:
        base, err = _resolve_current_root_folder(path, srv, default_to_current=True, allow_initial_root=True)
        if err is not None or base is None:
            return err or JSONResponse({"error": "invalid path"}, status_code=400)
        _ensure_config_for_path(str(base), ctx=srv)

    patch: Dict[str, Any] = {}

    # Basic validation + normalization
    if "always_include_docs_list" in payload:
        lst = payload.get("always_include_docs_list")
        if lst is not None:
            if not isinstance(lst, list) or not all(isinstance(x, str) for x in lst):
                return JSONResponse({"error": "always_include_docs_list must be an array of strings"}, status_code=400)
            patch["always_include_docs_list"] = lst
    if "custom_ext_map" in payload:
        mp = payload.get("custom_ext_map")
        if mp is not None:
            if not isinstance(mp, dict):
                return JSONResponse({"error": "custom_ext_map must be an object"}, status_code=400)
            patch["custom_ext_map"] = mp
    if "excluded_dirs" in payload:
        ed = payload.get("excluded_dirs")
        if ed is not None:
            if not isinstance(ed, list) or not all(isinstance(x, str) for x in ed):
                return JSONResponse({"error": "excluded_dirs must be array of strings"}, status_code=400)
            patch["excluded_dirs"] = ed
    if "excluded_files" in payload:
        ef = payload.get("excluded_files")
        if ef is not None:
            if not isinstance(ef, list) or not all(isinstance(x, str) for x in ef):
                return JSONResponse({"error": "excluded_files must be array of strings"}, status_code=400)
            patch["excluded_files"] = ef
    if "custom_excluded" in payload:
        ce = payload.get("custom_excluded")
        if ce is not None:
            if not isinstance(ce, list) or not all(isinstance(x, str) for x in ce):
                return JSONResponse({"error": "custom_excluded must be array of strings"}, status_code=400)
            patch["custom_excluded"] = ce

    # Advanced Groq + retrieval controls
    def _bool(k: str) -> None:
        if k in payload:
            v = payload.get(k)
            if v is None:
                return
            patch[k] = bool(v)

    def _int(k: str) -> JSONResponse | None:
        if k not in payload:
            return None
        try:
            v = payload.get(k)
            if v is None:
                return None
            patch[k] = int(v or 0)
            return None
        except Exception:
            return JSONResponse({"error": f"{k} must be integer"}, status_code=400)

    def _float(k: str) -> JSONResponse | None:
        if k not in payload:
            return None
        try:
            v = payload.get(k)
            if v is None:
                return None
            patch[k] = float(v or 0.0)
            return None
        except Exception:
            return JSONResponse({"error": f"{k} must be number"}, status_code=400)

    for k in ("groq_use_batch", "batch_progress", "groq_strict_mode"):
        _bool(k)
    res = _int("groq_max_workers")
    if isinstance(res, JSONResponse):
        return res
    res = _int("summary_token_limit")
    if isinstance(res, JSONResponse):
        return res
    res = _int("file_summary_chunk_tokens")
    if isinstance(res, JSONResponse):
        return res
    res = _int("file_summary_max_output_tokens")
    if isinstance(res, JSONResponse):
        return res
    res = _int("file_summary_unifier_max_output_tokens")
    if isinstance(res, JSONResponse):
        return res
    res = _int("aggregate_prompt_budget_tokens")
    if isinstance(res, JSONResponse):
        return res
    res = _int("aggregate_summary_max_output_tokens")
    if isinstance(res, JSONResponse):
        return res
    res = _int("aggregate_selector_max_output_tokens")
    if isinstance(res, JSONResponse):
        return res
    res = _int("aggregate_critic_max_output_tokens")
    if isinstance(res, JSONResponse):
        return res
    res = _int("summary_prompt_version")
    if isinstance(res, JSONResponse):
        return res
    res = _float("groq_request_timeout_seconds")
    if isinstance(res, JSONResponse):
        return res
    res = _int("groq_summary_retry_budget")
    if isinstance(res, JSONResponse):
        return res

    for k in ("auto_refresh_summaries", "summarize_vendored", "fallback_to_code_on_summary_error", "strict_summary_required"):
        _bool(k)
    if "summary_default_mode" in payload:
        v = payload.get("summary_default_mode")
        if v is not None:
            if not isinstance(v, str):
                return JSONResponse({"error": "summary_default_mode must be string"}, status_code=400)
            vv = v.strip().upper()
            allowed = {
                "CODE",
                "SKELETON",
                "CODE_PLUS_SKELETON",
                "SUMMARY_SHORT",
                "SUMMARY_LONG",
                "SUMMARY_SHORT_PLUS_SKELETON",
                "SUMMARY_LONG_PLUS_SKELETON",
                "CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON",
                "CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON",
                "CODE_PLUS_SHORT",
                "CODE_PLUS_LONG",
                "API_SURFACE",
                "SIGNATURES",
            }
            if vv not in allowed:
                allowed_text = ", ".join(sorted(allowed))
                return JSONResponse({"error": f"summary_default_mode must be one of: {allowed_text}"}, status_code=400)
            patch["summary_default_mode"] = vv
    if "summary_change_strategy" in payload:
        v = payload.get("summary_change_strategy")
        if v is not None:
            if not isinstance(v, str):
                return JSONResponse({"error": "summary_change_strategy must be string"}, status_code=400)
            vv = v.strip().lower()
            if vv not in {"mtime_size", "sha256", "git"}:
                return JSONResponse({"error": "summary_change_strategy must be one of: mtime_size, sha256, git"}, status_code=400)
            patch["summary_change_strategy"] = vv
    if "summary_prompt_version" in patch and patch["summary_prompt_version"] < 1:
        return JSONResponse({"error": "summary_prompt_version must be >= 1"}, status_code=400)

    if "embedding_provider" in payload:
        v = payload.get("embedding_provider")
        if v is not None:
            if not isinstance(v, str):
                return JSONResponse({"error": "embedding_provider must be string"}, status_code=400)
            vv = v.strip().lower()
            if vv not in {"groq", "openai", "none"}:
                return JSONResponse({"error": "embedding_provider must be one of: groq, openai, none"}, status_code=400)
            patch["embedding_provider"] = vv
    if "retrieval_backend" in payload:
        v = payload.get("retrieval_backend")
        if v is not None:
            if not isinstance(v, str):
                return JSONResponse({"error": "retrieval_backend must be string"}, status_code=400)
            vv = v.strip().lower()
            if vv not in {"embeddings", "file_search"}:
                return JSONResponse({"error": "retrieval_backend must be one of: embeddings, file_search"}, status_code=400)
            patch["retrieval_backend"] = vv

    # Merge naming and footer display
    for k in ("use_folder_name", "append_datetime", "footer_final_only", "show_bytes_footer"):
        _bool(k)

    # UI preferences
    res = _int("ui_font_size")
    if isinstance(res, JSONResponse):
        return res
    _bool("ui_high_contrast")
    _bool("ui_compact_mode")
    _bool("show_totals_columns")
    _bool("show_density_column")
    res = _int("ui_watch_debounce_ms")
    if isinstance(res, JSONResponse):
        return res
    res = _int("watch_sample_limit")
    if isinstance(res, JSONResponse):
        return res

    # Output + merge defaults
    for k in (
        "include_file_tree",
        "include_json_manifest",
        "output_markdown_fences",
        "output_markdown_toc",
        "remove_empty",
        "enumerate_lines",
        "apply_token_filters_to_transforms",
    ):
        _bool(k)
    for k in ("custom_start_label", "custom_end_label", "custom_tree_start_label", "custom_tree_end_label", "content_mode", "prompt_template", "structure_mode"):
        if k in payload:
            v = payload.get(k)
            if v is None:
                continue
            if not isinstance(v, str):
                return JSONResponse({"error": f"{k} must be string"}, status_code=400)
            patch[k] = v
    res = _int("split_into_n")
    if isinstance(res, JSONResponse):
        return res
    res = _int("token_limit")
    if isinstance(res, JSONResponse):
        return res

    # Optional: allow flat filter defaults updates (store under v23 nested last_filters)
    lf_patch: Dict[str, Any] = {}
    res = _int("token_min")
    if isinstance(res, JSONResponse):
        return res
    if "token_min" in patch:
        lf_patch["token_min"] = patch.pop("token_min")
    res = _int("token_max")
    if isinstance(res, JSONResponse):
        return res
    if "token_max" in patch:
        lf_patch["token_max"] = patch.pop("token_max")

    res = _float("size_min")
    if isinstance(res, JSONResponse):
        return res
    if "size_min" in patch:
        lf_patch["size_min"] = patch.pop("size_min")
    res = _float("size_max")
    if isinstance(res, JSONResponse):
        return res
    if "size_max" in patch:
        lf_patch["size_max"] = patch.pop("size_max")

    if "name_pattern" in payload:
        v = payload.get("name_pattern")
        if v is not None:
            if not isinstance(v, str):
                return JSONResponse({"error": "name_pattern must be string"}, status_code=400)
            lf_patch["name_pattern"] = str(v or "")

    if "date_after" in payload:
        if payload.get("date_after") is not None:
            lf_patch["date_after"] = _parse_date_to_ts(payload.get("date_after"))
    if "date_before" in payload:
        if payload.get("date_before") is not None:
            lf_patch["date_before"] = _parse_date_to_ts(payload.get("date_before"))

    if "categories" in payload:
        cats = payload.get("categories")
        if cats is not None:
            if not isinstance(cats, list) or not all(isinstance(x, str) for x in cats):
                return JSONResponse({"error": "categories must be an array of strings"}, status_code=400)
            lf_patch["categories"] = cats

    if lf_patch:
        patch["last_filters"] = lf_patch

    with srv.config_lock:
        if patch:
            srv.config.update_config(patch)
        cfg = srv.config.get_config()

    return JSONResponse({"ok": True, "settings": {
        "always_include_docs_list": cfg.get("always_include_docs_list", []),
        "custom_ext_map": cfg.get("custom_ext_map", {}),
        "excluded_dirs": cfg.get("excluded_dirs", []),
        "excluded_files": cfg.get("excluded_files", []),
        "custom_excluded": cfg.get("custom_excluded", []),
        "groq_use_batch": cfg.get("groq_use_batch", False),
        "batch_progress": cfg.get("batch_progress", False),
        "groq_max_workers": cfg.get("groq_max_workers", 0),
        "summary_token_limit": cfg.get("summary_token_limit", 0),
        "file_summary_chunk_tokens": cfg.get("file_summary_chunk_tokens", 0),
        "file_summary_max_output_tokens": cfg.get("file_summary_max_output_tokens", 0),
        "file_summary_unifier_max_output_tokens": cfg.get("file_summary_unifier_max_output_tokens", 0),
        "aggregate_prompt_budget_tokens": cfg.get("aggregate_prompt_budget_tokens", 0),
        "aggregate_summary_max_output_tokens": cfg.get("aggregate_summary_max_output_tokens", 0),
        "aggregate_selector_max_output_tokens": cfg.get("aggregate_selector_max_output_tokens", 0),
        "aggregate_critic_max_output_tokens": cfg.get("aggregate_critic_max_output_tokens", 0),
        "summary_default_mode": cfg.get("summary_default_mode", "CODE"),
        "auto_refresh_summaries": cfg.get("auto_refresh_summaries", True),
        "summarize_vendored": cfg.get("summarize_vendored", False),
        "fallback_to_code_on_summary_error": cfg.get("fallback_to_code_on_summary_error", False),
        "summary_change_strategy": cfg.get("summary_change_strategy", "mtime_size"),
        "summary_prompt_version": cfg.get("summary_prompt_version", 7),
        "groq_request_timeout_seconds": cfg.get("groq_request_timeout_seconds", 120.0),
        "groq_summary_retry_budget": cfg.get("groq_summary_retry_budget", 2),
        "strict_summary_required": cfg.get("strict_summary_required", False),
        "embedding_provider": cfg.get("embedding_provider", "groq"),
        "retrieval_backend": cfg.get("retrieval_backend", "embeddings"),
        "groq_strict_mode": cfg.get("groq_strict_mode", True),
        "use_folder_name": cfg.get("use_folder_name", False),
        "append_datetime": cfg.get("append_datetime", False),
        "footer_final_only": cfg.get("footer_final_only", False),
        "ui_font_size": cfg.get("ui_font_size", 10),
        "ui_high_contrast": cfg.get("ui_high_contrast", False),
        "ui_compact_mode": cfg.get("ui_compact_mode", False),
        "show_totals_columns": cfg.get("show_totals_columns", False),
        "show_density_column": cfg.get("show_density_column", False),
        "show_bytes_footer": cfg.get("show_bytes_footer", True),
        "ui_watch_debounce_ms": cfg.get("ui_watch_debounce_ms", 800),
        "watch_sample_limit": cfg.get("watch_sample_limit", 3000),
        "include_file_tree": cfg.get("include_file_tree", True),
        "include_json_manifest": cfg.get("include_json_manifest", False),
        "output_markdown_fences": cfg.get("output_markdown_fences", False),
        "output_markdown_toc": cfg.get("output_markdown_toc", False),
        "remove_empty": cfg.get("remove_empty", False),
        "enumerate_lines": cfg.get("enumerate_lines", False),
        "apply_token_filters_to_transforms": cfg.get("apply_token_filters_to_transforms", False),
        "custom_start_label": cfg.get("custom_start_label", "======= START OF FILE: {file_display} ======="),
        "custom_end_label": cfg.get("custom_end_label", "======= END OF FILE: {file_display} ======="),
        "custom_tree_start_label": cfg.get("custom_tree_start_label", "======= FILE TREE ======="),
        "custom_tree_end_label": cfg.get("custom_tree_end_label", "======= END OF FILE TREE ======="),
        "content_mode": cfg.get("content_mode", "code_only"),
        "prompt_template": cfg.get("prompt_template", ""),
        "structure_mode": cfg.get("structure_mode", "single_file"),
        "split_into_n": cfg.get("split_into_n", 5),
        "token_limit": cfg.get("token_limit", 1_048_576),
    }})


@app.post("/api/settings/excluded/reset")
def post_settings_reset_excluded(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    ctx = _require_ctx()
    if not isinstance(payload, dict):
        payload = {}
    path = payload.get("path")
    base, err = _resolve_current_root_folder(path, ctx, default_to_current=True, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    _ensure_config_for_path(str(base), ctx=ctx)
    reset_dirs = bool(payload.get("dirs", True))
    reset_files = bool(payload.get("files", True))
    reset_custom = bool(payload.get("custom", False))
    patch: Dict[str, Any] = {}
    if reset_dirs:
        try:
            patch["excluded_dirs"] = sorted(getattr(ctx.processor, "default_excluded_dirs", set()))
        except Exception:
            patch["excluded_dirs"] = []
    if reset_files:
        try:
            patch["excluded_files"] = sorted(getattr(ctx.processor, "default_excluded_files", set()))
        except Exception:
            patch["excluded_files"] = []
    if reset_custom:
        patch["custom_excluded"] = []
    if patch:
        ctx.config.update_config(patch)
    return JSONResponse({"ok": True, **patch})


@app.post("/api/settings/always_include/reset")
def post_settings_reset_always_include(payload: Dict[str, Any] = Body(None)) -> JSONResponse:
    ctx = _require_ctx()
    path = payload.get("path") if isinstance(payload, dict) else None
    base, err = _resolve_current_root_folder(path, ctx, default_to_current=True, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    _ensure_config_for_path(str(base), ctx=ctx)
    defaults = _default_always_include_docs_list()
    ctx.config.update_config({"always_include_docs_list": defaults})
    return JSONResponse({"ok": True, "always_include_docs_list": defaults})


def _normalise_custom_excluded_paths(paths: object, base: Path) -> list[str]:
    if not isinstance(paths, list):
        return []
    base_resolved = base.resolve()
    out: list[str] = []
    seen: set[str] = set()
    for item in paths:
        if not isinstance(item, str):
            continue
        raw = item.strip()
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = base / p
        try:
            rel = p.resolve().relative_to(base_resolved).as_posix()
        except Exception:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


def _set_custom_excluded(srv: ServerContext, paths: list[str]) -> None:
    with srv.config_lock:
        srv.config.update_config({"custom_excluded": paths})
        cfg = srv.config.get_config()
    srv.core.custom_excluded = set(paths)
    try:
        srv.core._apply_custom_excluded()
    except Exception:
        srv.core.configure(cfg)


@app.post("/api/custom_excluded/clear")
def post_custom_excluded_clear(payload: Dict[str, Any] = Body(None)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") if isinstance(payload, dict) else None
    base, err = _resolve_current_root_folder(path, srv, default_to_current=True, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    _ensure_config_for_path(str(base), ctx=srv)
    _set_custom_excluded(srv, [])
    return JSONResponse({"ok": True, "custom_excluded": []})


@app.post("/api/custom_excluded/toggle")
def post_custom_excluded_toggle(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    base, err = _resolve_current_root_folder(path, srv, default_to_current=True, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    _ensure_config_for_path(str(base), ctx=srv)
    cfg = srv.config.get_config()
    rels = _normalise_custom_excluded_paths(payload.get("paths"), base)
    if not rels:
        return JSONResponse({"error": "paths must contain valid files under root"}, status_code=400)

    cfg = srv.config.get_config()
    current = cfg.get("custom_excluded", [])
    cur_set = {str(x) for x in current if isinstance(x, str) and x}
    for rel in rels:
        if rel in cur_set:
            cur_set.discard(rel)
        else:
            cur_set.add(rel)
    updated = sorted(cur_set)
    _set_custom_excluded(srv, updated)
    return JSONResponse({"ok": True, "custom_excluded": updated, "updated": len(rels)})


# ----------------- Context Pack -----------------
@app.get("/api/context_pack/subsystems")
def get_context_pack_subsystems(path: str | None = None) -> JSONResponse:
    srv = _require_ctx()
    base, err = _resolve_current_root_folder(path, srv, default_to_current=True, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    try:
        names = list_manifest_subsystems(str(base))
    except ContextPackError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "subsystems": names})


@app.post("/api/context_pack/build")
def post_context_pack_build(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base, err = _resolve_current_root_folder(path, srv, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    _ensure_config_for_path(str(base), ctx=srv)

    subsystem = payload.get("subsystem")
    if not isinstance(subsystem, str) or not subsystem.strip():
        return JSONResponse({"error": "subsystem must be a non-empty string"}, status_code=400)
    mode = payload.get("mode") or "subsystem"
    if not isinstance(mode, str) or mode.strip().lower() not in {"subsystem", "audit"}:
        return JSONResponse({"error": "mode must be 'subsystem' or 'audit'"}, status_code=400)

    output_path = payload.get("output_path")
    if output_path is not None:
        if not isinstance(output_path, str):
            return JSONResponse({"error": "output_path must be a string"}, status_code=400)
        output_path = output_path.strip() or None
        if output_path is not None:
            try:
                policy = _path_policy_for(
                    base,
                    allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
                )
                output_path = str(policy.resolve_output_file(output_path, default_name="context_pack.txt").path)
            except PathPolicyError as exc:
                return _path_error_response(exc)

    return_text = payload.get("return_text")
    if return_text is None:
        return_text = output_path is None
    else:
        return_text = bool(return_text)

    try:
        cfg = srv.config.get_config()
        result = srv.processor.build_pack(
            str(base),
            subsystem.strip(),
            mode.strip(),
            output_path,
            return_text=return_text,
            app_config=cfg,
        )
    except ContextPackError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    if result.output_path:
        _allow_path(result.output_path)

    return JSONResponse({
        "ok": True,
        "result": {
            "subsystem": result.subsystem,
            "mode": result.mode,
            "output_text": result.output_text,
            "output_path": result.output_path,
            "included_files": list(result.included_files),
            "warnings": list(result.warnings),
        },
    })


# ----------------- Pair / Restore -----------------
def _base_for_output_policy(ctx: ServerContext) -> Path:
    return Path(ctx.config.get_config().get("last_directory", "") or ".")


def _resolve_relative_path_list(values: object, *, field_name: str) -> tuple[list[str] | None, JSONResponse | None]:
    if not isinstance(values, list) or not values:
        return None, JSONResponse({"error": f"{field_name} must be a non-empty array"}, status_code=400)
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            return None, JSONResponse({"error": f"{field_name} must contain strings"}, status_code=400)
        rel = item.strip().replace("\\", "/")
        parts = Path(rel).parts
        if Path(rel).is_absolute() or ".." in parts:
            return None, JSONResponse({"error": f"{field_name} items must be relative paths under the source root"}, status_code=403)
        normalized = Path(*parts).as_posix() if parts else ""
        if not normalized or normalized == ".":
            return None, JSONResponse({"error": f"{field_name} must contain valid relative paths"}, status_code=400)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out, None


def _resolve_restore_source(raw: str, base: Path, ctx: ServerContext) -> tuple[str | None, JSONResponse | None]:
    p = Path(raw)
    if not p.is_absolute():
        p = base / p
    try:
        p = p.resolve()
    except Exception:
        pass
    if not p.exists() or not p.is_file():
        return None, JSONResponse({"error": f"source is not a file: {p}"}, status_code=400)
    policy = _path_policy_for(base, ctx=ctx)
    if not policy.can_stat(p):
        return None, JSONResponse({"error": "access denied"}, status_code=403)
    return str(p), None


def _source_grants_from_payload(payload: Dict[str, Any], base: Path) -> tuple[list[PathGrant] | None, JSONResponse | None]:
    raw_grants = payload.get("source_grants")
    if raw_grants is None:
        raw_grants = payload.get("source_roots")
    if raw_grants is None:
        raw_grants = []
    if not isinstance(raw_grants, list):
        return None, JSONResponse({"error": "source_grants must be an array"}, status_code=400)
    grants: list[PathGrant] = []
    for item in raw_grants:
        if not isinstance(item, str) or not item.strip():
            return None, JSONResponse({"error": "source_grants must contain non-empty paths"}, status_code=400)
        raw = item.strip()
        path = Path(raw)
        if not path.is_absolute():
            path = base / path
        try:
            path = path.resolve()
        except Exception:
            return None, JSONResponse({"error": "invalid source grant path"}, status_code=400)
        if not path.exists():
            return None, JSONResponse({"error": f"source grant does not exist: {path}"}, status_code=400)
        grants.append(
            PathGrant(
                path=path,
                purpose=PathGrantPurpose.SOURCE,
                kind=PathGrantKind.FOLDER if path.is_dir() else PathGrantKind.FILE,
                root=base,
                created_at=_utc_now_iso(),
            )
        )
    return grants, None


def _resolve_source_path(
    raw: object,
    base: Path,
    ctx: ServerContext,
    payload: Dict[str, Any],
    *,
    field_name: str,
    allow_dir: bool = True,
) -> tuple[str | None, JSONResponse | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, JSONResponse({"error": f"{field_name} must be a non-empty string"}, status_code=400)
    grants, err = _source_grants_from_payload(payload, base)
    if err is not None or grants is None:
        return None, err or JSONResponse({"error": "invalid source grants"}, status_code=400)
    p = Path(raw.strip())
    if not p.is_absolute():
        p = base / p
    try:
        p = p.resolve()
    except Exception:
        return None, JSONResponse({"error": f"invalid {field_name}"}, status_code=400)
    if not p.exists() or (not allow_dir and not p.is_file()):
        return None, JSONResponse({"error": f"{field_name} does not exist: {p}"}, status_code=400)
    if p.is_dir() and not allow_dir:
        return None, JSONResponse({"error": f"{field_name} must be a file: {p}"}, status_code=400)
    policy = PathPolicy(base, grants=tuple(ctx.path_grants) + tuple(grants))
    if not policy.can_source(p):
        return None, JSONResponse(
            {"error": f"{field_name} requires active-root scope or explicit source_grants"},
            status_code=403,
        )
    return str(p), None


@app.post("/api/pair_codebases", response_model=PairResponse)
def post_pair_codebases(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    src_a = payload.get("src_a")
    src_b = payload.get("src_b")
    dest = payload.get("dest_dir")
    if not all(isinstance(x, str) and x for x in (src_a, src_b, dest)):
        return JSONResponse({"error": "src_a, src_b, dest_dir are required"}, status_code=400)
    try:
        ctx = _require_ctx()
        base = _base_for_output_policy(ctx)
        src_a, err = _resolve_source_path(src_a, base, ctx, payload, field_name="src_a")
        if err is not None or src_a is None:
            return err or JSONResponse({"error": "src_a is required"}, status_code=400)
        src_b, err = _resolve_source_path(src_b, base, ctx, payload, field_name="src_b")
        if err is not None or src_b is None:
            return err or JSONResponse({"error": "src_b is required"}, status_code=400)
        try:
            policy = _path_policy_for(
                base,
                ctx=ctx,
                allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
            )
            dest = str(policy.resolve_output_folder(dest, default_name="paired_codebases").path)
        except PathPolicyError as exc:
            return _path_error_response(exc)
        keep_structure = bool(payload.get("keep_structure", True))
        label_a = payload.get("label_a") if isinstance(payload.get("label_a"), str) else None
        label_b = payload.get("label_b") if isinstance(payload.get("label_b"), str) else None
        pair_selected = bool(payload.get("pair_selected", False))
        selected_rel = payload.get("selected_rel_paths")
        if pair_selected:
            selected_clean, err = _resolve_relative_path_list(selected_rel, field_name="selected_rel_paths")
            if err is not None or not selected_clean:
                return err or JSONResponse({"error": "selected_rel_paths must be a non-empty array"}, status_code=400)
            ctx.processor.pair_selected_files(
                src_a,
                selected_clean,
                src_b,
                dest,
                keep_structure=keep_structure,
                label_a=label_a,
                label_b=label_b,
            )
        else:
            ctx.processor.pair_codebases(
                src_a,
                src_b,
                dest,
                keep_structure=keep_structure,
                label_a=label_a,
                label_b=label_b,
            )
        return JSONResponse({"ok": True, "dest": dest})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/pair_ai", response_model=PairAiResponse)
def post_pair_ai(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    src_a = payload.get("src_a")
    src_b = payload.get("src_b")
    if not all(isinstance(x, str) and x for x in (src_a, src_b)):
        return JSONResponse({"error": "src_a and src_b are required"}, status_code=400)
    try:
        ctx = _require_ctx()
        base = _base_for_output_policy(ctx)
        src_a, err = _resolve_source_path(src_a, base, ctx, payload, field_name="src_a")
        if err is not None or src_a is None:
            return err or JSONResponse({"error": "src_a is required"}, status_code=400)
        src_b, err = _resolve_source_path(src_b, base, ctx, payload, field_name="src_b")
        if err is not None or src_b is None:
            return err or JSONResponse({"error": "src_b is required"}, status_code=400)
        out_raw = payload.get("output_file") or payload.get("output_path")
        out_path: Path | None = None
        if isinstance(out_raw, str) and out_raw.strip():
            try:
                policy = _path_policy_for(
                    base,
                    ctx=ctx,
                    allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
                )
                out_path = policy.resolve_output_file(out_raw.strip(), default_name="pair_ai_report.txt").path
            except PathPolicyError as exc:
                return _path_error_response(exc)
        cfg = ctx.config.get_config()
        preflight = _provider_preflight_decision(
            ctx,
            "pair_ai",
            cfg=cfg,
            root=str(cfg.get("last_directory") or ""),
            target=f"{src_a} :: {src_b}",
            paths=[],
            requires_confirmation=True,
            risks=["Pair+AI compares matching files through the AI summarizer."],
            estimated_provider_calls=1,
        )
        gate = _provider_confirmation_response(preflight, payload)
        if gate is not None:
            return gate
        with provider_call_counter() as counter:
            results = ctx.processor.compare_codebases_ai(src_a, src_b)
        report = ctx.processor.format_comparison_report(results, src_a=src_a, src_b=src_b)
        provider_accounting = _merge_observed_provider_accounting(
            _provider_accounting_from_preflight(preflight),
            counter.snapshot(),
        )
        if out_path is not None:
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(report, encoding="utf-8", errors="replace")
                _allow_path(out_path)
                return JSONResponse({
                    "report": report,
                    "output_path": str(out_path),
                    "provider_accounting": provider_accounting,
                })
            except Exception as exc:
                return JSONResponse({"error": f"failed to write output: {exc}"}, status_code=500)
        return JSONResponse({"report": report, "provider_accounting": provider_accounting})
    except Exception as exc:
        logger.exception("pair_ai failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/restore", response_model=RestoreResponse)
def post_restore(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    source = payload.get("source")
    dest = payload.get("dest_dir")
    if not all(isinstance(x, str) and x for x in (source, dest)):
        return JSONResponse({"error": "source and dest_dir required"}, status_code=400)
    try:
        ctx = _require_ctx()
        base = _base_for_output_policy(ctx)
        source, err = _resolve_source_path(source, base, ctx, payload, field_name="source", allow_dir=False)
        if err is not None or source is None:
            return err or JSONResponse({"error": "source and dest_dir required"}, status_code=400)
        try:
            policy = _path_policy_for(
                base,
                ctx=ctx,
                allow_external_output=_safe_bool(payload.get("allow_external_output", False)),
            )
            dest = str(policy.resolve_output_folder(dest, default_name="restored_files").path)
        except PathPolicyError as exc:
            return _path_error_response(exc)
        custom_start = payload.get("custom_start_label") if isinstance(payload.get("custom_start_label"), str) else None
        custom_end = payload.get("custom_end_label") if isinstance(payload.get("custom_end_label"), str) else None
        ctx.processor.restore_from_merged(
            source,
            dest,
            custom_start_label=custom_start or "======= START OF FILE: {file_display} =======",
            custom_end_label=custom_end or "======= END OF FILE: {file_display} =======",
        )
        return JSONResponse({"ok": True, "dest": dest})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ----------------- Diagnostics -----------------
@app.post("/api/diagnostics")
def post_diagnostics(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be an object"}, status_code=400)
    srv = _require_ctx()
    cfg = srv.config.get_config()
    path = payload.get("path") or cfg.get("last_directory")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    base, err = _resolve_current_root_folder(path, srv, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)

    try:
        _ensure_config_for_path(str(base), ctx=srv)
        cfg = srv.config.get_config()
        manual_lookup = _manual_lookup_snapshot()
        _ctx, ui, visible, _dups, _warnings, _pattern_hits = _scan_visible(base, payload, cfg, manual_lookup=manual_lookup)
        show_all = _payload_show_all(payload)
        ignore_persisted = bool(payload.get("ignore_custom_excluded", False)) or show_all
        diag_cfg = dict(cfg)
        diag_cfg["_show_all"] = show_all
        diag_cfg["_ignore_custom_excluded"] = ignore_persisted
        if ignore_persisted:
            diag_cfg["custom_excluded"] = []
        orig_dirs = set(getattr(srv.processor, "excluded_dirs", set()))
        orig_files = set(getattr(srv.processor, "excluded_files", set()))
        try:
            if show_all:
                srv.processor.excluded_dirs = set()
                srv.processor.excluded_files = set()
            report = diagnostics_service.run_diagnostics(srv.processor, ui, visible, config=diag_cfg)
        finally:
            srv.processor.excluded_dirs = orig_dirs
            srv.processor.excluded_files = orig_files
        return JSONResponse({"report": report})
    except Exception as exc:
        logger.exception("diagnostics failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ----------------- Diagnostics: why this file -----------------
@app.post("/api/why")
def post_why(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    srv = _require_ctx()
    path = payload.get("path") or srv.config.get_config().get("last_directory")
    file_path = payload.get("file_path")
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path must be a non-empty string"}, status_code=400)
    if not isinstance(file_path, str) or not file_path:
        return JSONResponse({"error": "file_path must be provided"}, status_code=400)
    base, err = _resolve_current_root_folder(path, srv, allow_initial_root=True)
    if err is not None or base is None:
        return err or JSONResponse({"error": "invalid path"}, status_code=400)
    _ensure_config_for_path(str(base), ctx=srv)
    p = Path(file_path)
    if not p.is_absolute():
        p = base / p
    try:
        p = p.resolve()
    except Exception:
        pass
    try:
        base_resolved = base.resolve()
        _ = p.relative_to(base_resolved)
    except Exception:
        return JSONResponse({"error": "access denied"}, status_code=403)
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": f"file not found: {p}"}, status_code=404)

    cfg = srv.config.get_config()
    show_all = _payload_show_all(payload)
    ignore_custom_excluded = bool(payload.get("ignore_custom_excluded", False)) or show_all
    filters_payload = payload.get("filters")
    filters = filters_payload if isinstance(filters_payload, dict) else {}

    # Build UiState using the same mapping as /api/scan so diagnostics match the current UI knobs.
    try:
        ui = _payload_to_ui_state(payload, cfg, warnings=None)
    except Exception:
        ui = _payload_to_ui_state({"path": str(base), "filters": filters}, cfg, warnings=None)

    # Prefer existing scan metadata to keep token counts consistent with the table.
    m: FileMeta | None = None
    ctx_root = base
    try:
        scan_ctx = srv.core.scan_ctx
        if scan_ctx and scan_ctx.files:
            ctx_root = scan_ctx.root
            p_abs = os.path.abspath(str(p))
            for meta in scan_ctx.files:
                try:
                    if os.path.abspath(str(meta.abs_path)) == p_abs:
                        m = meta
                        break
                except Exception:
                    continue
    except Exception:
        m = None
        ctx_root = base

    if m is None:
        ctx = srv.processor.scan_file_list(str(base), [str(p)])
        if not ctx.files:
            return JSONResponse({"error": "failed to read file metadata"}, status_code=500)
        m = ctx.files[0]
        ctx_root = ctx.root

    rel = m.rel_path.as_posix()
    abs_str = os.path.abspath(str(m.abs_path))
    manual_lookup = _manual_lookup_snapshot()
    is_forced = abs_str in manual_lookup

    # Resolve git helpers (best-effort, cached by CoreController).
    try:
        srv.core.configure(cfg)
    except Exception:
        pass
    git_changed = None
    vendored = None
    try:
        git_changed = srv.core._resolve_git_changed(ui)
    except Exception:
        git_changed = None
    try:
        vendored = srv.core._resolve_vendored(ui)
    except Exception:
        vendored = None

    reasons: list[str] = []

    # Always include docs (README, LICENSE, etc.)
    try:
        if scan_service.always_include(m, ui):
            reasons.append("Included by Always-Include list")
    except Exception:
        pass

    # Category gate
    try:
        if not ui.filters.category_mask.get(m.category, True):
            reasons.append("Category filter disabled")
    except Exception:
        pass

    # Token range (respect transform token settings when enabled)
    tok_val = m.token_count or 0
    try:
        if ui.filters.apply_token_filters_to_transforms:
            tok_val = srv.processor.processed_token_count(
                str(m.abs_path),
                enumerate_lines=ui.filters.enumerate_lines,
                remove_empty=ui.filters.remove_empty,
            )
    except Exception:
        pass
    if m.read_error:
        reasons.append("Token count unavailable (read error)")
    try:
        if not (ui.filters.token_min <= tok_val <= ui.filters.token_max):
            reasons.append("Outside token range")
    except Exception:
        pass

    # Size range
    try:
        size_kb = (m.size_bytes or 0) / 1024.0
        if not (ui.filters.size_min_kb <= size_kb <= ui.filters.size_max_kb):
            reasons.append("Outside size range")
    except Exception:
        pass

    # Name pattern
    import re as _re
    import fnmatch as _fn
    pattern = ui.filters.name_pattern or ""
    if pattern:
        if ui.filters.name_glob:
            parts = [pp.strip() for pp in _re.split(r"[;,]", pattern) if pp.strip()]
            rel_cmp = rel.lower()
            parts_cmp = [pp.lower() for pp in parts]
            if parts and not any(_fn.fnmatchcase(rel_cmp, pp) for pp in parts_cmp):
                reasons.append("Glob pattern would exclude")
        else:
            try:
                compiled = _re.compile(pattern, _re.IGNORECASE)
                if not compiled.search(rel):
                    reasons.append("Regex pattern would exclude")
            except _re.error:
                reasons.append("Regex pattern invalid")

    # Content search
    if ui.filters.content_search:
        try:
            def _resolve_max_bytes(value_kb: int, default_kb: int) -> int:
                try:
                    kb = int(value_kb)
                except Exception:
                    kb = 0
                if kb <= 0:
                    kb = default_kb
                return kb * 1024

            max_bytes = _resolve_max_bytes(
                int(ui.filters.content_max_kb or 0),
                scan_service.DEFAULT_CONTENT_MAX_KB,
            )
            ok = scan_service._content_match(  # type: ignore[attr-defined]
                m.abs_path,
                ui.filters.content_search,
                ui.filters.content_regex,
                ui.filters.content_case,
                max_bytes,
            )
        except Exception:
            ok = False
        if not ok:
            reasons.append("Content search did not match")

    # Date filters
    try:
        if ui.filters.date_after_ts and m.mtime < ui.filters.date_after_ts:
            reasons.append("Before 'Date After'")
        if ui.filters.date_before_ts and m.mtime > ui.filters.date_before_ts:
            reasons.append("After 'Date Before'")
    except Exception:
        pass

    # Vendored / git-changed hints
    if vendored is not None and rel in vendored:
        reasons.append("Marked as vendored")
    if git_changed is not None and rel not in git_changed:
        reasons.append("Not changed in selected git range")

    # Custom excluded
    custom_list = cfg.get("custom_excluded", [])
    custom_set = {x for x in custom_list if isinstance(x, str) and x}
    if rel in custom_set:
        reasons.append("Custom excluded (ignored)" if ignore_custom_excluded else "Custom excluded")

    # Duplicate marker (if available from the last scan)
    try:
        if abs_str in (srv.core.dup_set or set()):
            reasons.append("Duplicate content")
    except Exception:
        pass

    # Determine current kept/hidden state for this file (approximate, matching scan_service.apply_filters order).
    kept = True
    if not is_forced:
        if rel in custom_set and not ignore_custom_excluded:
            kept = False
        else:
            try:
                if scan_service.always_include(m, ui):
                    kept = True
                else:
                    if git_changed is not None and rel not in git_changed:
                        kept = False
                    if kept and vendored is not None and rel in vendored:
                        kept = False
                    if kept and not ui.filters.category_mask.get(m.category, True):
                        kept = False
                    if kept and not (ui.filters.token_min <= tok_val <= ui.filters.token_max):
                        kept = False
                    if kept:
                        size_kb = (m.size_bytes or 0) / 1024.0
                        if not (ui.filters.size_min_kb <= size_kb <= ui.filters.size_max_kb):
                            kept = False
                    if kept and pattern:
                        if ui.filters.name_glob:
                            parts = [pp.strip() for pp in _re.split(r"[;,]", pattern) if pp.strip()]
                            if parts and not any(_fn.fnmatchcase(rel, pp) for pp in parts):
                                kept = False
                        else:
                            try:
                                compiled = _re.compile(pattern, _re.IGNORECASE)
                                if not compiled.search(rel):
                                    kept = False
                            except _re.error:
                                kept = False
                    if kept and ui.filters.content_search and not ok:
                        kept = False
                    if kept and ui.filters.date_after_ts and m.mtime < ui.filters.date_after_ts:
                        kept = False
                    if kept and ui.filters.date_before_ts and m.mtime > ui.filters.date_before_ts:
                        kept = False
            except Exception:
                pass

            # Best-effort duplicate hiding signal.
            try:
                if kept and ui.filters.hide_duplicates and abs_str in (srv.core.dup_set or set()):
                    kept = False
            except Exception:
                pass

    return JSONResponse(
        {
            "kept": kept,
            "reasons": reasons,
            "meta": _serialize_file(m, ctx_root, manual_lookup=manual_lookup, dup_set=(srv.core.dup_set or set())),
        }
    )
