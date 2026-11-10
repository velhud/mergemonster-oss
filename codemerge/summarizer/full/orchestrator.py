from __future__ import annotations

import concurrent.futures
import json
from hashlib import sha256
import time
from pathlib import Path
from typing import Callable, Optional

from codemerge.core.normalize import canonical_path
from codemerge.filemerger.types import FileProcessorLike as FileProcessor
from codemerge.llm._limits import resolve_prompt_budget
from codemerge.llm._summarize_service import TruncationError
from codemerge.llm.reducer import pack_items_by_token_budget
from codemerge.llm.summarize import (
    SYSTEM_PROMPT,
    aggregate_prompt_fingerprint,
    build_folder_summary_prompt,
    build_group_summary_prompt,
    build_repo_summary_prompt,
    fetch_ai_text_summary,
    resolve_full_summary_model,
)
from codemerge.models import FileMeta
from codemerge.summarizer.shared.aggregate_cache import AggregateCache, resolve_aggregate_cache_db_path
from codemerge.summarizer.shared.audit import (
    build_audit_artifact,
    parse_audit_artifact,
    serialize_audit_artifact,
)
from codemerge.summarizer.shared.change_pointers import (
    augment_change_pointer_section,
    augment_missing_heading_sections,
    augment_missing_inventory_change_pointers,
)
from codemerge.summarizer.shared.evidence import (
    build_inventory_list,
    build_tree_snapshot,
    compute_inputs_hash,
    compute_inventory_hash,
    format_anchor_blocks,
    format_evidence_blocks,
    is_anchor_filename,
)
from codemerge.summarizer.shared.orchestrator import ensure_summaries
from codemerge.summarizer.shared.quality import (
    sanitize_scoped_summary_paths,
    sanitize_template_placeholder_literals,
    validate_full_summary_contract,
)
from codemerge.summarizer.shared.scope import (
    build_manifest,
    filter_folder_metas,
    folder_rel_path,
    inventory_in_scope,
    posix,
    safe_rel,
    split_direct_vs_descendant,
)

try:
    from codemerge.llm.summarize import PLAIN_SUMMARY_VERSION
except Exception:
    PLAIN_SUMMARY_VERSION = 1

SCOPE_DEFINITION_VERSION = 1
SCOPE_LABEL = "text_only_excluding_binary_assets"
FULL_PIPELINE_VERSION = 8
_REPO_SIGNAL_ANCHOR_PRIORITY = {
    "main.py": 0,
    "server.py": 1,
    "router.py": 2,
    "merge_service.py": 3,
    "api.py": 4,
    "config.py": 5,
    "controller.py": 6,
    "runner.py": 7,
    "orchestrator.py": 8,
}

_build_manifest = build_manifest


def _allow_repo_external_path_mentions(target_kind: str) -> bool:
    return str(target_kind or "").strip().lower() == "folder"


def _finalize_full_summary(
    summary_text: str,
    *,
    manifest_paths: list[str],
    scope_rel: str,
    inventory_items: list[str],
    required_headings: list[str],
    target_kind: str,
    root_abs: str,
) -> str:
    rendered = augment_change_pointer_section(
        summary_text,
        candidate_paths=manifest_paths,
        headings=("## Where to change X",),
    )
    rendered, _invalid_paths = sanitize_scoped_summary_paths(
        rendered,
        inventory_items=inventory_items,
        manifest_paths=manifest_paths,
        scope_path_prefix="" if scope_rel in {"", "."} else scope_rel,
        repo_root_abs=root_abs,
        allow_existing_repo_paths_outside_scope=_allow_repo_external_path_mentions(target_kind),
        replacement_style="redact",
    )
    rendered = sanitize_template_placeholder_literals(rendered)
    validation = validate_full_summary_contract(
        rendered,
        required_headings=required_headings,
        inventory_items=inventory_items,
        scope_path_prefix="" if scope_rel in {"", ".", "/"} else scope_rel,
    )
    missing_headings = list(validation.get("missing_headings") or [])
    if missing_headings:
        rendered = augment_missing_heading_sections(
            rendered,
            required_headings=required_headings,
        )
        validation = validate_full_summary_contract(
            rendered,
            required_headings=required_headings,
            inventory_items=inventory_items,
            scope_path_prefix="" if scope_rel in {"", ".", "/"} else scope_rel,
        )
    uncovered_inventory_items = list(validation.get("uncovered_inventory_items") or [])
    if uncovered_inventory_items:
        rendered = augment_missing_inventory_change_pointers(
            rendered,
            missing_items=uncovered_inventory_items,
            headings=("## Where to change X",),
        )
        validation = validate_full_summary_contract(
            rendered,
            required_headings=required_headings,
            inventory_items=inventory_items,
            scope_path_prefix="" if scope_rel in {"", ".", "/"} else scope_rel,
        )
    missing_headings = list(validation.get("missing_headings") or [])
    placeholder_leaks = list(validation.get("placeholder_leaks") or [])
    uncovered_inventory_items = list(validation.get("uncovered_inventory_items") or [])
    if missing_headings or placeholder_leaks or uncovered_inventory_items:
        problems: list[str] = []
        if missing_headings:
            problems.append("missing headings: " + ", ".join(missing_headings))
        if placeholder_leaks:
            problems.append("placeholder leaks: " + ", ".join(placeholder_leaks))
        if uncovered_inventory_items:
            problems.append("uncovered inventory: " + ", ".join(uncovered_inventory_items))
        raise RuntimeError("Full summary contract validation failed: " + "; ".join(problems))
    return rendered


def _full_runtime_fingerprint(app_config: dict) -> str:
    file_kind = str(app_config.get("full_summary_file_kind", "long") or "long")
    try:
        group_ratio = float(app_config.get("aggregate_group_prompt_budget_ratio", 0.6) or 0.6)
    except Exception:
        group_ratio = 0.6
    payload = {
        "pipeline_version": FULL_PIPELINE_VERSION,
        "full_summary_file_kind": "short" if file_kind.lower().startswith("short") else "long",
        "strict_long_summary_fallback_kind": None,
        "summary_anchor_max_bytes": int(app_config.get("summary_anchor_max_bytes", 64 * 1024) or 64 * 1024),
        "aggregate_group_prompt_budget_ratio": group_ratio,
        "aggregate_group_prompt_budget_tokens": int(app_config.get("aggregate_group_prompt_budget_tokens", 0) or 0),
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def _repo_signal_anchor_items(
    *,
    in_scope: list[FileMeta],
    direct_metas: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    anchor_max_bytes: int,
) -> list[tuple[str, str]]:
    try:
        signal_limit = int(app_config.get("summary_repo_signal_anchor_limit", 6) or 6)
    except Exception:
        signal_limit = 6
    signal_limit = max(signal_limit, 0)
    if signal_limit <= 0:
        return []
    try:
        signal_max_bytes = int(app_config.get("summary_signal_anchor_max_bytes", 16 * 1024) or 16 * 1024)
    except Exception:
        signal_max_bytes = 16 * 1024
    signal_max_bytes = max(1, min(anchor_max_bytes, signal_max_bytes))
    direct_paths = {canonical_path(str(meta.abs_path)) for meta in direct_metas}
    candidates: list[tuple[int, int, str, str]] = []
    for meta in in_scope:
        abs_path = canonical_path(str(meta.abs_path))
        if abs_path in direct_paths:
            continue
        rel_path = posix(meta.rel_path.as_posix())
        name = Path(rel_path).name
        priority = _REPO_SIGNAL_ANCHOR_PRIORITY.get(name)
        if priority is None or int(meta.size_bytes) > signal_max_bytes:
            continue
        content = file_processor.read_file_content(str(meta.abs_path))
        if content is None:
            continue
        candidates.append((priority, len(rel_path.split("/")), rel_path, content))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(rel_path, content) for _priority, _depth, rel_path, content in candidates[:signal_limit]]


def _plain_summary_inner(
    *,
    target_kind: str,
    target_path: str,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    cache: AggregateCache | None = None,
    progress_cb: Optional[Callable[[str, int | None, int | None], None]] = None,
    scope_source: str = "caller_scan_files",
) -> str:
    if target_kind not in {"folder", "repo"}:
        raise ValueError(f"invalid target_kind: {target_kind!r}")
    cache_local = cache or AggregateCache(resolve_aggregate_cache_db_path(app_config))
    manifest_hash_value = ""
    inputs_hash_value = None
    inventory_hash_value = None
    runtime_fingerprint = _full_runtime_fingerprint(app_config)
    inventory_count = 0
    file_count = 0
    skipped_count = 0
    manifest_paths: list[str] = []
    skipped_items: list[dict] = []
    reduction_layers = 0
    group_counts: list[int] = []
    reduction_plan: list[dict[str, object]] = []
    evidence_items: list[tuple[str, str]] = []
    anchor_paths: list[str] = []
    candidate_summary_text: str | None = None
    phase_timings_ms: dict[str, int] = {}
    started_at = time.time()
    try:
        def _emit(stage: str, done: int | None = None, total: int | None = None) -> None:
            if progress_cb:
                progress_cb(stage, done, total)

        scoped_metas, folder_prefix = filter_folder_metas(target_path, root_abs, scan_files)
        in_scope, skipped, _errors = inventory_in_scope(metas=scoped_metas, file_processor=file_processor)
        file_count = len(in_scope)
        skipped_count = len(skipped)
        skipped_items = [dict(item) for item in skipped]
        manifest_paths, manifest_hash_value = build_manifest(in_scope)
        version = int(app_config.get("summary_prompt_version", PLAIN_SUMMARY_VERSION) or PLAIN_SUMMARY_VERSION)
        prompt_fp = aggregate_prompt_fingerprint(target_kind, "full", version)
        model = resolve_full_summary_model(app_config)

        direct_metas, children = split_direct_vs_descendant(in_scope, folder_prefix)
        direct_paths = [str(meta.abs_path) for meta in direct_metas]
        file_kind = str(app_config.get("full_summary_file_kind", "long") or "long")
        file_kind = "short" if file_kind.lower().startswith("short") else "long"

        inventory_items: list[str] = []
        for meta in direct_metas:
            rel_local = folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix)
            if rel_local:
                inventory_items.append(rel_local)
        for child_name in sorted(children.keys()):
            inventory_items.append(f"{child_name}/")
        inventory_items = sorted(set(inventory_items))
        inventory_count = len(inventory_items)
        inventory_hash_value = compute_inventory_hash(inventory_items)
        inventory_list_text = build_inventory_list(inventory_items)
        scope_rel = safe_rel(target_path, root_abs)
        if scope_rel in {"", "."}:
            scope_rel = "/"
        if target_kind == "repo":
            required_headings = [
                "## System overview",
                "## Major subsystems",
                "## Entry points / how it runs",
                "## Cross-subsystem flows",
                "## Dependencies / infrastructure",
                "## Side effects map",
                "## Where to change X",
                "## Uncertainties",
                "## Coverage note",
            ]
        else:
            required_headings = [
                "## Role in the system",
                "## Structure map",
                "## Interfaces / entry points",
                "## Key flows through this area",
                "## Dependencies",
                "## Side effects zones",
                "## Where to change X",
                "## Uncertainties",
                "## Coverage note",
            ]

        _emit("inventory", inventory_count, inventory_count)

        def _file_progress(done: int, total: int) -> None:
            _emit("file_summaries", done, total)

        file_summary_started = time.time()
        file_summaries, _ = ensure_summaries(
            file_paths=direct_paths,
            kind=file_kind,
            file_processor=file_processor,
            app_config=app_config,
            root=root_abs,
            strict_required=True,
            progress_cb=_file_progress,
        )
        phase_timings_ms["file_summaries_ms"] = int(round((time.time() - file_summary_started) * 1000))

        for meta in direct_metas:
            abs_path = str(meta.abs_path)
            summary_text = file_summaries.get(abs_path)
            if summary_text is None:
                raise RuntimeError(f"Full strict: missing summary for {abs_path}")
            rel_local = folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix)
            evidence_items.append((rel_local, summary_text))

        child_summaries: dict[str, str] = {}
        total_children = len(children)
        if total_children:
            child_started = time.time()
            max_workers = int(app_config.get("folder_summary_max_workers", 0) or 0)
            if max_workers <= 0:
                max_workers = min(8, max(1, total_children))
            if total_children == 1:
                max_workers = 1
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {}
                for child_name in sorted(children.keys()):
                    child_abs = str(Path(target_path) / child_name)
                    future = executor.submit(
                        _plain_summary_inner,
                        target_kind="folder",
                        target_path=child_abs,
                        root_abs=root_abs,
                        scan_files=scan_files,
                        file_processor=file_processor,
                        app_config=app_config,
                        cache=None,
                        progress_cb=progress_cb,
                        scope_source=scope_source,
                    )
                    future_map[future] = child_name
                completed = 0
                for future in concurrent.futures.as_completed(future_map):
                    child_name = future_map[future]
                    child_summaries[child_name] = future.result()
                    completed += 1
                    _emit("child_folders", completed, total_children)
            phase_timings_ms["child_folders_ms"] = int(round((time.time() - child_started) * 1000))

        for child_name in sorted(child_summaries.keys()):
            evidence_items.append((f"{child_name}/", child_summaries[child_name]))

        inputs_hash_value = compute_inputs_hash(evidence_items)
        base_log_context = {
            "target_kind": target_kind,
            "target_path": canonical_path(target_path),
            "prompt_fp": prompt_fp,
        }

        cached = cache_local.get(
            kind=target_kind,
            path=target_path,
            model=model,
            prompt_fp=prompt_fp,
            mode="full",
            manifest_hash=manifest_hash_value,
            inputs_hash=inputs_hash_value,
        )
        if (
            cached
            and cached.get("status") == "ok"
            and cached.get("manifest_hash") == manifest_hash_value
            and cached.get("inputs_hash") == inputs_hash_value
        ):
            raw = cached.get("summary_text")
            if isinstance(raw, str) and raw:
                cached_reduction_layers = int(cached.get("reduction_layers") or 0)
                cached_batch_count = int(cached.get("batch_count") or 0)
                cached_group_counts_raw = cached.get("group_counts")
                cached_group_counts: list[int] = []
                if isinstance(cached_group_counts_raw, str) and cached_group_counts_raw.strip():
                    for part in cached_group_counts_raw.split(","):
                        try:
                            cached_group_counts.append(int(part.strip()))
                        except Exception:
                            continue
                existing_audit = parse_audit_artifact(cached.get("audit_json"))
                cached_runtime_fingerprint = str((existing_audit or {}).get("runtime_fingerprint") or "")
                if cached_runtime_fingerprint == runtime_fingerprint:
                    audit_payload = build_audit_artifact(
                        mode="full",
                        target_kind=target_kind,
                        target_path=canonical_path(target_path),
                        scope_source=scope_source,
                        pipeline_version=FULL_PIPELINE_VERSION,
                        manifest_hash=manifest_hash_value,
                        inputs_hash=inputs_hash_value,
                        inventory_hash=inventory_hash_value,
                        runtime_fingerprint=runtime_fingerprint,
                        file_count=file_count,
                        skipped_count=skipped_count,
                        inventory_count=inventory_count,
                        manifest_paths=manifest_paths,
                        skipped_items=skipped_items,
                        inventory_items=inventory_items,
                        selected_evidence_paths=[path for path, _ in evidence_items],
                        anchor_paths=list((existing_audit or {}).get("anchor_paths") or []),
                        candidate_summary=(existing_audit or {}).get("candidate_summary") or raw,
                        final_summary=raw,
                        reduction_layers=cached_reduction_layers,
                        group_counts=cached_group_counts,
                        reduction_plan=list((existing_audit or {}).get("reduction_plan") or []),
                        provider_calls={
                            "aggregate_total": 0,
                            "final": 0,
                            "group": 0,
                        },
                        cache={
                            "hit": True,
                            "revalidated": False,
                            "batch_count": cached_batch_count,
                        },
                        timings={
                            **phase_timings_ms,
                            "total_ms": int(round((time.time() - started_at) * 1000)),
                        },
                        validation=dict((existing_audit or {}).get("validation") or {}),
                        errors=list((existing_audit or {}).get("errors") or []),
                    )
                    cache_local.put_ok(
                        kind=target_kind,
                        path=target_path,
                        mode="full",
                        manifest_hash=manifest_hash_value,
                        inputs_hash=inputs_hash_value,
                        inventory_hash=inventory_hash_value,
                        model=model,
                        prompt_fp=prompt_fp,
                        summary_text=raw,
                        file_count=file_count,
                        skipped_count=skipped_count,
                        reduction_layers=cached_reduction_layers,
                        batch_count=cached_batch_count,
                        group_counts=cached_group_counts_raw,
                        inventory_count=inventory_count,
                        scope_label=SCOPE_LABEL,
                        scope_definition_version=SCOPE_DEFINITION_VERSION,
                        audit_json=serialize_audit_artifact(audit_payload),
                    )
                    _emit("cached", 1, 1)
                    return raw
        if not evidence_items:
            evidence_items = [
                ("EMPTY", "No in-scope file or subfolder summaries available.")
            ]

        def _summarize(prompt_text: str, *, log_context: dict | None = None) -> str:
            merged = dict(base_log_context)
            if log_context:
                merged.update(log_context)
            return fetch_ai_text_summary(
                prompt_text=prompt_text,
                app_config=app_config,
                token_counter=file_processor.count_tokens,
                log_context=merged,
                model_name_override=model,
            )

        profile = str(app_config.get("summary_profile", "merge") or "merge")
        repo_name = Path(root_abs).name
        anchor_max_bytes = int(app_config.get("summary_anchor_max_bytes", 64 * 1024) or 64 * 1024)

        anchor_items: list[tuple[str, str]] = []
        for meta in direct_metas:
            rel_local = folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix)
            if not rel_local:
                continue
            name = Path(rel_local).name
            if not is_anchor_filename(name):
                continue
            if int(meta.size_bytes) > anchor_max_bytes:
                continue
            content = file_processor.read_file_content(str(meta.abs_path))
            if content is None:
                continue
            anchor_items.append((rel_local, content))
        if target_kind == "repo":
            existing_anchor_paths = {path for path, _content in anchor_items}
            for rel_path, content in _repo_signal_anchor_items(
                in_scope=in_scope,
                direct_metas=direct_metas,
                file_processor=file_processor,
                app_config=app_config,
                anchor_max_bytes=anchor_max_bytes,
            ):
                if rel_path in existing_anchor_paths:
                    continue
                anchor_items.append((rel_path, content))
                existing_anchor_paths.add(rel_path)
        anchor_paths = [path for path, _ in anchor_items]

        tree_snapshot = build_tree_snapshot(
            [folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix) for meta in in_scope],
            max_depth=2,
        )
        anchor_blocks = format_anchor_blocks(anchor_items)

        system_prompt = SYSTEM_PROMPT or "You are a professional code summarizer."
        try:
            system_tokens = max(int(file_processor.count_tokens(system_prompt)), 1)
        except Exception:
            system_tokens = max(len(system_prompt), 1)

        def _prompt_tokens(prompt_text: str) -> int:
            try:
                return max(int(file_processor.count_tokens(prompt_text)), 1) + system_tokens
            except Exception:
                return max(len(prompt_text), 1) + system_tokens

        final_budget = resolve_prompt_budget(app_config, model_name=model, call_kind="aggregate")
        group_ratio_raw = app_config.get("aggregate_group_prompt_budget_ratio", 0.6)
        try:
            group_ratio = float(group_ratio_raw)
        except Exception:
            group_ratio = 0.6
        group_ratio = min(max(group_ratio, 0.2), 0.9)
        group_budget = int(app_config.get("aggregate_group_prompt_budget_tokens", 0) or 0)
        if group_budget <= 0:
            group_budget = max(4_000, int(final_budget * group_ratio))
        group_budget = min(group_budget, final_budget)
        min_group_budget = max(2_000, int(group_budget * 0.2))

        group_overhead_prompt = build_group_summary_prompt(
            profile=profile,
            repo_name=repo_name,
            scope_rel=scope_rel or "/",
            layer_index=1,
            group_id="1",
            inventory_list=inventory_list_text,
            evidence_blocks="(none)",
        )
        group_overhead_tokens = _prompt_tokens(group_overhead_prompt)

        working_items = list(evidence_items)
        force_reduce = False
        group_budget_local = group_budget
        while True:
            evidence_blocks = format_evidence_blocks(working_items)
            if target_kind == "repo":
                final_prompt = build_repo_summary_prompt(
                    "full",
                    profile=profile,
                    repo_name=repo_name,
                    manifest_id=manifest_hash_value,
                    scope_label=SCOPE_LABEL,
                    scope_def_version=SCOPE_DEFINITION_VERSION,
                    inventory_list=inventory_list_text,
                    tree_snapshot=tree_snapshot,
                    anchor_blocks=anchor_blocks,
                    evidence_blocks=evidence_blocks,
                )
            else:
                final_prompt = build_folder_summary_prompt(
                    "full",
                    profile=profile,
                    repo_name=repo_name,
                    folder_rel=scope_rel,
                    manifest_id=manifest_hash_value,
                    scope_label=SCOPE_LABEL,
                    scope_def_version=SCOPE_DEFINITION_VERSION,
                    inventory_list=inventory_list_text,
                    tree_snapshot=tree_snapshot,
                    anchor_blocks=anchor_blocks,
                    evidence_blocks=evidence_blocks,
                )
            if not force_reduce and _prompt_tokens(final_prompt) <= final_budget:
                try:
                    summary_text = _summarize(final_prompt, log_context={"phase": "final"})
                    candidate_summary_text = summary_text
                    break
                except TruncationError:
                    force_reduce = True
            if len(working_items) == 1 and force_reduce:
                raise RuntimeError("Full summary truncated after maximum reduction.")
            while True:
                groups = pack_items_by_token_budget(
                    working_items,
                    token_counter=file_processor.count_tokens,
                    budget_tokens=group_budget_local,
                    overhead_reserve_tokens=group_overhead_tokens,
                )
                next_items: list[tuple[str, str]] = []
                truncation = False
                for idx, group in enumerate(groups, start=1):
                    prompt_text = build_group_summary_prompt(
                        profile=profile,
                        repo_name=repo_name,
                        scope_rel=scope_rel or "/",
                        layer_index=reduction_layers + 1,
                        group_id=str(idx),
                        inventory_list=inventory_list_text,
                        evidence_blocks=format_evidence_blocks(group),
                    )
                    try:
                        summary_text = _summarize(
                            prompt_text,
                            log_context={
                                "phase": "group",
                                "layer": reduction_layers + 1,
                                "group_index": idx,
                                "group_total": len(groups),
                            },
                        )
                    except TruncationError:
                        truncation = True
                        break
                    next_items.append((f"GROUP L{reduction_layers + 1} #{idx}", summary_text))
                if truncation:
                    if group_budget_local <= min_group_budget:
                        raise RuntimeError("Group summary truncated after minimum budget.")
                    group_budget_local = max(min_group_budget, int(group_budget_local * 0.7))
                    continue
                reduction_layers += 1
                group_counts.append(len(groups))
                reduction_plan.append(
                    {
                        "layer": reduction_layers,
                        "group_count": len(groups),
                        "group_sizes": [len(group) for group in groups],
                    }
                )
                _emit("reduction_layer", reduction_layers, len(groups))
                working_items = next_items
                force_reduce = False
                break

        batch_count = sum(group_counts)
        group_counts_str = ",".join(str(x) for x in group_counts) if group_counts else None
        final_text = _finalize_full_summary(
            summary_text,
            manifest_paths=manifest_paths,
            scope_rel=scope_rel,
            inventory_items=inventory_items,
            required_headings=required_headings,
            target_kind=target_kind,
            root_abs=root_abs,
        )
        validation = validate_full_summary_contract(
            final_text,
            required_headings=required_headings,
            inventory_items=inventory_items,
            scope_path_prefix="" if scope_rel in {"", ".", "/"} else scope_rel,
        )
        audit_payload = build_audit_artifact(
            mode="full",
            target_kind=target_kind,
            target_path=canonical_path(target_path),
            scope_source=scope_source,
            pipeline_version=FULL_PIPELINE_VERSION,
            manifest_hash=manifest_hash_value,
            inputs_hash=inputs_hash_value,
            inventory_hash=inventory_hash_value,
            runtime_fingerprint=runtime_fingerprint,
            file_count=file_count,
            skipped_count=skipped_count,
            inventory_count=inventory_count,
            manifest_paths=manifest_paths,
            skipped_items=skipped_items,
            inventory_items=inventory_items,
            selected_evidence_paths=[path for path, _ in evidence_items],
            anchor_paths=anchor_paths,
            candidate_summary=candidate_summary_text or summary_text,
            final_summary=final_text,
            reduction_layers=reduction_layers,
            group_counts=group_counts,
            reduction_plan=reduction_plan,
            provider_calls={
                "aggregate_total": 1 + batch_count,
                "final": 1,
                "group": batch_count,
            },
            cache={"hit": False, "revalidated": False, "batch_count": batch_count},
            timings={
                **phase_timings_ms,
                "total_ms": int(round((time.time() - started_at) * 1000)),
            },
            validation=validation,
        )

        cache_local.put_ok(
            kind=target_kind,
            path=target_path,
            mode="full",
            manifest_hash=manifest_hash_value,
            inputs_hash=inputs_hash_value,
            inventory_hash=inventory_hash_value,
            model=model,
            prompt_fp=prompt_fp,
            summary_text=final_text,
            file_count=file_count,
            skipped_count=skipped_count,
            reduction_layers=reduction_layers,
            batch_count=batch_count,
            group_counts=group_counts_str,
            inventory_count=inventory_count,
            scope_label=SCOPE_LABEL,
            scope_definition_version=SCOPE_DEFINITION_VERSION,
            audit_json=serialize_audit_artifact(audit_payload),
        )
        if progress_cb:
            progress_cb("final", 1, 1)
        return final_text
    except Exception as exc:
        try:
            version = int(app_config.get("summary_prompt_version", PLAIN_SUMMARY_VERSION) or PLAIN_SUMMARY_VERSION)
            prompt_fp = aggregate_prompt_fingerprint(target_kind, "full", version)
            model = resolve_full_summary_model(app_config)
            final_summary_error = None
            audit_payload = build_audit_artifact(
                mode="full",
                target_kind=target_kind,
                target_path=canonical_path(target_path),
                scope_source=scope_source,
                pipeline_version=FULL_PIPELINE_VERSION,
                manifest_hash=manifest_hash_value or "",
                inputs_hash=inputs_hash_value,
                inventory_hash=inventory_hash_value,
                runtime_fingerprint=runtime_fingerprint,
                file_count=file_count,
                skipped_count=skipped_count,
                inventory_count=inventory_count,
                manifest_paths=manifest_paths,
                skipped_items=skipped_items,
                inventory_items=inventory_items,
                selected_evidence_paths=[path for path, _ in evidence_items],
                anchor_paths=anchor_paths,
                candidate_summary=candidate_summary_text,
                final_summary=final_summary_error,
                reduction_layers=reduction_layers,
                group_counts=group_counts,
                reduction_plan=reduction_plan,
                provider_calls={},
                cache={"hit": False, "revalidated": False},
                timings={
                    **phase_timings_ms,
                    "total_ms": int(round((time.time() - started_at) * 1000)),
                },
                validation={},
                errors=[str(exc)],
            )
            cache_local.put_error(
                kind=target_kind,
                path=target_path,
                mode="full",
                manifest_hash=manifest_hash_value or "",
                inputs_hash=inputs_hash_value,
                inventory_hash=inventory_hash_value,
                model=model,
                prompt_fp=prompt_fp,
                file_count=file_count,
                skipped_count=skipped_count,
                reduction_layers=0,
                batch_count=0,
                group_counts=None,
                inventory_count=inventory_count,
                scope_label=SCOPE_LABEL,
                scope_definition_version=SCOPE_DEFINITION_VERSION,
                audit_json=serialize_audit_artifact(audit_payload),
            )
        except Exception:
            pass
        raise
    finally:
        if cache is None:
            try:
                cache_local.close()
            except Exception:
                pass


def ensure_folder_summary_full(
    *,
    folder_abs: str,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    progress_cb: Optional[Callable[[str, int | None, int | None], None]] = None,
    scope_source: str = "caller_scan_files",
) -> str:
    return _plain_summary_inner(
        target_kind="folder",
        target_path=folder_abs,
        root_abs=root_abs,
        scan_files=scan_files,
        file_processor=file_processor,
        app_config=app_config,
        progress_cb=progress_cb,
        scope_source=scope_source,
    )


def ensure_repo_summary_full(
    *,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    progress_cb: Optional[Callable[[str, int | None, int | None], None]] = None,
    scope_source: str = "caller_scan_files",
) -> str:
    return _plain_summary_inner(
        target_kind="repo",
        target_path=root_abs,
        root_abs=root_abs,
        scan_files=scan_files,
        file_processor=file_processor,
        app_config=app_config,
        progress_cb=progress_cb,
        scope_source=scope_source,
    )


__all__ = [
    "ensure_folder_summary_full",
    "ensure_repo_summary_full",
    "_build_manifest",
]
