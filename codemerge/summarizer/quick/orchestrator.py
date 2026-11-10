from __future__ import annotations

import json
from hashlib import sha256
import re
import time
from pathlib import Path

from codemerge.filemerger.types import FileProcessorLike as FileProcessor
from codemerge.llm._limits import resolve_max_output_tokens
from codemerge.llm._summarize_service import TruncationError
from codemerge.llm.summarize import (
    aggregate_prompt_fingerprint,
    build_folder_summary_prompt,
    build_quick_selector_prompt,
    build_repo_summary_prompt,
    fetch_ai_text_summary,
    resolve_full_summary_model,
)
from codemerge.models import FileMeta
from codemerge.summarizer.shared.aggregate_cache import AggregateCache, resolve_aggregate_cache_db_path
from codemerge.summarizer.shared.audit import build_audit_artifact, parse_audit_artifact, serialize_audit_artifact
from codemerge.summarizer.shared.change_pointers import augment_change_pointer_section
from codemerge.summarizer.shared.evidence import (
    ChildStat,
    build_child_stats,
    build_inventory_list,
    build_tree_snapshot,
    compute_inputs_hash,
    compute_inventory_hash,
    format_anchor_blocks,
    format_evidence_blocks,
    is_anchor_filename,
    parse_selector_output,
)
from codemerge.summarizer.shared.orchestrator import ensure_summaries
from codemerge.summarizer.shared.quality import (
    sanitize_scoped_summary_paths,
    sanitize_template_placeholder_literals,
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

SCOPE_DEFINITION_VERSION = 1
SCOPE_LABEL = "text_only_excluding_binary_assets"
QUICK_PIPELINE_VERSION = 11
DEFAULT_QUICK_SELECT_ALL_LIMIT = 12
_REPO_SIGNAL_FILE_PRIORITY = {
    "merge_service.py": 0,
    "server.py": 1,
    "router.py": 2,
    "main.py": 3,
    "orchestrator.py": 4,
    "api.py": 5,
    "config.py": 6,
}


def _summary_profile(app_config: dict) -> str:
    return str(app_config.get("summary_profile", "merge") or "merge")


def _allow_repo_external_path_mentions(target_kind: str) -> bool:
    return str(target_kind or "").strip().lower() == "folder"


def _quick_select_all_limit(app_config: dict) -> int:
    try:
        value = int(app_config.get("quick_summary_select_all_inventory_limit", DEFAULT_QUICK_SELECT_ALL_LIMIT) or 0)
    except Exception:
        value = DEFAULT_QUICK_SELECT_ALL_LIMIT
    return max(0, value)


def _quick_runtime_fingerprint(target_kind: str, app_config: dict) -> str:
    quick_file_kind = str(app_config.get("quick_summary_file_kind", "short") or "short")
    payload = {
        "target_kind": str(target_kind or "").strip().lower(),
        "pipeline_version": QUICK_PIPELINE_VERSION,
        "quick_summary_max_depth": int(app_config.get("quick_summary_max_depth", 2) or 2),
        "quick_summary_file_kind": "short" if quick_file_kind.lower().startswith("short") else "long",
        "quick_summary_select_all_inventory_limit": _quick_select_all_limit(app_config),
        "selection_cap": 25,
        "repo_force_folder_pick": True,
        "signal_file_extra": 2,
        "nested_signal_file_extra": 4,
        "nested_signal_per_folder": 3,
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def _quick_pick_count(total: int, *, default: int, max_picks: int) -> int:
    if total <= 0:
        return 0
    if total <= default:
        return total
    return min(max_picks, max(default, int(total * 0.25)))


def _heuristic_picks(inventory: list[str], n_picks: int) -> list[str]:
    if n_picks <= 0:
        return []
    priority = []
    keywords = (
        "readme",
        "main",
        "app",
        "server",
        "api",
        "cli",
        "index",
        "entry",
        "config",
        "ui",
        "core",
        "worker",
        "cache",
        "auth",
        "db",
    )
    for item in inventory:
        low = item.lower()
        score = 0
        for kw in keywords:
            if kw in low:
                score += 1
        if low.endswith("/"):
            score += 1
        priority.append((score, item))
    priority.sort(key=lambda x: (-x[0], x[1]))
    picks = []
    for _, item in priority:
        if item not in picks:
            picks.append(item)
        if len(picks) >= n_picks:
            break
    return picks


def _score_folder_item(item: str) -> tuple[int, int, str]:
    low = item.lower().rstrip("/")
    keywords = (
        ("codemerge", 5),
        ("summarizer", 5),
        ("filemerger", 4),
        ("merge", 3),
        ("jsui", 4),
        ("ui", 3),
        ("server", 3),
        ("llm", 3),
        ("core", 3),
        ("agent", 3),
        ("api", 3),
        ("web", 3),
        ("app", 3),
        ("src", 3),
        ("backend", 2),
        ("frontend", 2),
        ("prune", 2),
        ("cli", 2),
        ("service", 2),
    )
    score = 0
    for keyword, weight in keywords:
        if keyword in low:
            score += weight
    if low.startswith("."):
        score -= 2
    if "test" in low or "doc" in low:
        score -= 1
    return score, len(low), item


def _score_signal_file(item: str) -> tuple[int, int, str]:
    low = item.lower()
    name = Path(low).name
    keywords = (
        ("main", 3),
        ("app", 3),
        ("server", 3),
        ("api", 3),
        ("cli", 2),
        ("config", 2),
        ("router", 2),
        ("entry", 2),
        ("orchestrator", 3),
        ("summary", 2),
        ("summarizer", 2),
        ("cache", 2),
        ("audit", 3),
        ("quality", 2),
        ("scope", 2),
        ("manifest", 2),
        ("merge", 3),
        ("service", 2),
        ("processor", 2),
        ("watch", 1),
        ("model", 1),
    )
    score = 0
    for keyword, weight in keywords:
        if keyword in low:
            score += weight
    priority = _REPO_SIGNAL_FILE_PRIORITY.get(name)
    if priority is not None:
        score += max(0, 6 - priority)
    if "/services/" in low:
        score += 2
    if "/docs/" in low or "corpus" in low:
        score -= 4
    if "test" in low:
        score -= 2
    if low.startswith("."):
        score -= 2
    if low.endswith((".md", ".txt", ".json", ".toml", ".yaml", ".yml")):
        score -= 2
    if low.endswith(".bak"):
        score -= 3
    return score, len(low), item


def _ensure_repo_folder_pick(selected: list[str], inventory: list[str]) -> list[str]:
    folder_items = [item for item in inventory if item.endswith("/")]
    if not folder_items:
        return list(selected)
    if any(item.endswith("/") for item in selected):
        return list(selected)
    keywords = (
        "codemerge",
        "src",
        "app",
        "server",
        "api",
        "core",
        "service",
        "services",
        "ui",
        "web",
        "client",
        "backend",
        "frontend",
        "lib",
        "cli",
    )
    ranked: list[tuple[int, int, str]] = []
    for item in folder_items:
        low = item.lower()
        score = 0
        for keyword in keywords:
            if keyword in low:
                score += 2
        if low.startswith("."):
            score -= 2
        if "test" in low or "docs" in low:
            score -= 1
        ranked.append((score, len(low), item))
    ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
    best = ranked[0][2]
    return list(dict.fromkeys([best, *selected]))


def _ensure_folder_breadth(selected: list[str], inventory: list[str], *, minimum: int) -> list[str]:
    folder_items = [item for item in inventory if item.endswith("/")]
    if not folder_items or minimum <= 0:
        return list(selected)
    picks = list(selected)
    selected_folders = {item for item in picks if item.endswith("/")}
    ranked = sorted((_score_folder_item(item) for item in folder_items), key=lambda row: (-row[0], row[1], row[2]))
    required = {item for _score, _length, item in ranked[:minimum]}
    for _score, _length, item in ranked:
        if item not in required or item in selected_folders:
            continue
        picks.append(item)
        selected_folders.add(item)
    return list(dict.fromkeys(picks))


def _ensure_signal_file_coverage(selected: list[str], inventory: list[str], *, max_extra: int) -> list[str]:
    if max_extra <= 0:
        return list(selected)
    picks = list(selected)
    selected_set = set(picks)
    candidates = [item for item in inventory if not item.endswith("/") and item not in selected_set]
    ranked = sorted((_score_signal_file(item) for item in candidates), key=lambda row: (-row[0], row[1], row[2]))
    added = 0
    for score, _length, item in ranked:
        if score <= 0:
            break
        picks.append(item)
        selected_set.add(item)
        added += 1
        if added >= max_extra:
            break
    return list(dict.fromkeys(picks))


def _scope_prefix(scope_rel: str | None) -> str:
    raw = str(scope_rel or "").strip()
    if raw in {"", ".", "/"}:
        return ""
    return raw.strip("/")


def _rooted_display_path(scope_rel: str | None, rel_local: str) -> str:
    local = str(rel_local or "").strip().strip("/")
    if not local:
        return ""
    prefix = _scope_prefix(scope_rel)
    if not prefix:
        return local
    return f"{prefix}/{local}"


def _module_alias(rel_path: str) -> str | None:
    normalized = str(rel_path or "").strip().strip("/")
    if not normalized.endswith(".py"):
        return None
    return normalized[:-3].replace("/", ".")


def _replace_path_aliases(summary_text: str, alias_map: dict[str, str]) -> str:
    rendered = str(summary_text or "")
    for alias, rooted in sorted(alias_map.items(), key=lambda item: (-len(item[0]), item[0])):
        if not alias or not rooted or alias == rooted:
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_/-]){re.escape(alias)}(?![A-Za-z0-9_/-]|\.[A-Za-z0-9_])"
        )
        rendered = pattern.sub(rooted, rendered)
    return rendered


def _normalize_quick_summary_paths(
    summary_text: str,
    *,
    scope_rel: str,
    inventory_items: list[str],
    in_scope: list[FileMeta],
    folder_prefix: str,
) -> str:
    alias_map: dict[str, str] = {}
    rooted_inventory = [_rooted_display_path(scope_rel, item.rstrip("/")) + ("/" if item.endswith("/") else "") for item in inventory_items]
    for local, rooted in zip(inventory_items, rooted_inventory):
        local_norm = str(local or "").strip()
        rooted_norm = str(rooted or "").strip()
        if local_norm and rooted_norm and local_norm != rooted_norm:
            alias_map.setdefault(local_norm, rooted_norm)
        if not _scope_prefix(scope_rel):
            if local_norm:
                alias_map.setdefault(f"/{local_norm}", rooted_norm or local_norm)
                alias_map.setdefault(f"//{local_norm}", rooted_norm or local_norm)

    local_paths: list[str] = []
    for meta in in_scope:
        rel_local = folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix)
        if not rel_local:
            continue
        local_paths.append(rel_local)
        rooted = _rooted_display_path(scope_rel, rel_local)
        if rooted and rooted != rel_local:
            alias_map.setdefault(rel_local, rooted)
        dotted = _module_alias(rel_local)
        if dotted:
            alias_map.setdefault(dotted, rooted or rel_local)

    basename_counts: dict[str, int] = {}
    for rel_local in local_paths:
        name = Path(rel_local).name
        if name:
            basename_counts[name] = basename_counts.get(name, 0) + 1
    for rel_local in local_paths:
        rooted = _rooted_display_path(scope_rel, rel_local)
        name = Path(rel_local).name
        if name and basename_counts.get(name) == 1:
            alias_map.setdefault(name, rooted or rel_local)
            if not _scope_prefix(scope_rel):
                alias_map.setdefault(f"/{name}", rooted or rel_local)
                alias_map.setdefault(f"//{name}", rooted or rel_local)

    normalized = _replace_path_aliases(summary_text, alias_map)
    if not _scope_prefix(scope_rel):
        normalized = normalized.replace("# `//`", "# `/`")
    return normalized


def _collect_nested_signal_file_items(
    *,
    selected: list[str],
    in_scope: list[FileMeta],
    folder_prefix: str,
    max_total: int,
    max_per_folder: int,
) -> list[tuple[str, FileMeta]]:
    if max_total <= 0 or max_per_folder <= 0:
        return []
    selected_folders = [item.rstrip("/") for item in selected if item.endswith("/")]
    if not selected_folders:
        return []
    folder_set = set(selected_folders)
    ranked: list[tuple[int, int, str, str, FileMeta]] = []
    for meta in in_scope:
        rel_local = folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix)
        if not rel_local or "/" not in rel_local:
            continue
        top_folder = rel_local.split("/", 1)[0]
        if top_folder not in folder_set:
            continue
        score, length, _ = _score_signal_file(rel_local)
        if score <= 0:
            continue
        ranked.append((score, length, top_folder, rel_local, meta))
    selected_nested: list[tuple[str, FileMeta]] = []
    per_folder_counts: dict[str, int] = {}
    for _score, _length, folder_name, rel_local, meta in sorted(
        ranked,
        key=lambda row: (-row[0], row[1], row[2], row[3]),
    ):
        if per_folder_counts.get(folder_name, 0) >= max_per_folder:
            continue
        if any(existing_path == rel_local for existing_path, _existing_meta in selected_nested):
            continue
        selected_nested.append((rel_local, meta))
        per_folder_counts[folder_name] = per_folder_counts.get(folder_name, 0) + 1
        if len(selected_nested) >= max_total:
            break
    return selected_nested


def _auto_pick_inventory_items(inventory: list[str], *, app_config: dict) -> list[str]:
    if len(inventory) > _quick_select_all_limit(app_config):
        return []
    visible = [item for item in inventory if not Path(item.rstrip("/")).name.startswith(".")]
    return list(dict.fromkeys(visible or list(inventory)))


def _collect_anchor_blocks(
    direct_metas: list[FileMeta],
    folder_prefix: str,
    file_processor: FileProcessor,
    *,
    max_bytes: int,
) -> list[tuple[str, str]]:
    anchors: list[tuple[str, str]] = []
    for meta in direct_metas:
        rel_local = folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix)
        if not rel_local:
            continue
        name = Path(rel_local).name
        if not is_anchor_filename(name):
            continue
        if int(meta.size_bytes) > max_bytes:
            continue
        content = file_processor.read_file_content(str(meta.abs_path))
        if content is None:
            continue
        anchors.append((rel_local, content))
    return anchors


def _summarize_quick(
    *,
    target_kind: str,
    target_path: str,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    depth: int,
    cache: AggregateCache,
    scope_source: str = "caller_scan_files",
) -> str:
    started_at = time.time()
    cache_enabled = depth == 0
    skipped_count = 0
    manifest_hash_value = ""
    inventory_items: list[str] = []
    inventory_hash = ""
    evidence_items: list[tuple[str, str]] = []
    anchor_paths: list[str] = []
    errors: list[str] = []
    selector_calls = 0
    phase_timings_ms: dict[str, int] = {}
    version = int(app_config.get("summary_prompt_version", 1) or 1)
    prompt_fp = aggregate_prompt_fingerprint(target_kind, "quick", version)
    runtime_fingerprint = _quick_runtime_fingerprint(target_kind, app_config)
    model = resolve_full_summary_model(app_config)
    try:
        scoped_metas, folder_prefix = filter_folder_metas(target_path, root_abs, scan_files)
        in_scope, skipped, _errors = inventory_in_scope(metas=scoped_metas, file_processor=file_processor)
        skipped_count = len(skipped)
        manifest_paths, manifest_hash_value = build_manifest(in_scope)
        direct_metas, children = split_direct_vs_descendant(in_scope, folder_prefix)

        for meta in direct_metas:
            rel_local = folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix)
            if rel_local:
                inventory_items.append(rel_local)
        for child_name in sorted(children.keys()):
            inventory_items.append(f"{child_name}/")
        inventory_items = sorted(set(inventory_items))
        inventory_hash = compute_inventory_hash(inventory_items)
        cached = (
            cache.get(
                kind=target_kind,
                path=target_path,
                model=model,
                prompt_fp=prompt_fp,
                mode="quick",
                manifest_hash=manifest_hash_value,
                inventory_hash=inventory_hash,
            )
            if cache_enabled
            else None
        )
        cached_audit = parse_audit_artifact(cached.get("audit_json")) if cached else None
        cached_selected_paths = [
            str(item)
            for item in ((cached_audit or {}).get("selected_evidence_paths") or [])
            if isinstance(item, str) and item
        ]
        cached_runtime_fingerprint = str((cached_audit or {}).get("runtime_fingerprint") or "")
        cached_pipeline_version = 0
        try:
            cached_pipeline_version = int((cached_audit or {}).get("pipeline_version") or 0)
        except Exception:
            cached_pipeline_version = 0

        profile = _summary_profile(app_config)
        repo_name = Path(root_abs).name
        scope_rel = safe_rel(target_path, root_abs)
        if scope_rel in {"", "."}:
            scope_rel = "/"

        if (
            cache_enabled
            and cached
            and cached.get("status") == "ok"
            and cached.get("manifest_hash") == manifest_hash_value
            and cached.get("inventory_hash") == inventory_hash
            and cached_pipeline_version == QUICK_PIPELINE_VERSION
            and cached_runtime_fingerprint == runtime_fingerprint
            and any(path.endswith("/") for path in cached_selected_paths)
        ):
            raw = cached.get("summary_text")
            if isinstance(raw, str) and raw:
                cached_inputs_hash = cached.get("inputs_hash")
                cached_inputs_hash = cached_inputs_hash if isinstance(cached_inputs_hash, str) else None
                raw, sanitized_invalid_paths = sanitize_scoped_summary_paths(
                    raw,
                    inventory_items=inventory_items,
                    manifest_paths=manifest_paths,
                    scope_path_prefix="" if scope_rel in {"", "."} else scope_rel,
                    repo_root_abs=root_abs,
                    allow_existing_repo_paths_outside_scope=_allow_repo_external_path_mentions(target_kind),
                    replacement_style="redact",
                )
                if sanitized_invalid_paths:
                    errors.append(
                        "sanitized invalid cached path references: " + ", ".join(sorted(sanitized_invalid_paths))
                    )
                raw = sanitize_template_placeholder_literals(raw)
                cached_anchor_paths = [
                    str(item)
                    for item in ((cached_audit or {}).get("anchor_paths") or [])
                    if isinstance(item, str) and item
                ]
                audit_payload = build_audit_artifact(
                    mode="quick",
                    target_kind=target_kind,
                    target_path=target_path,
                    scope_source=scope_source,
                    manifest_hash=manifest_hash_value,
                    inputs_hash=cached_inputs_hash,
                    inventory_hash=inventory_hash,
                    runtime_fingerprint=runtime_fingerprint,
                    file_count=len(in_scope),
                    skipped_count=skipped_count,
                    inventory_count=len(inventory_items),
                    manifest_paths=manifest_paths,
                    inventory_items=inventory_items,
                    selected_evidence_paths=cached_selected_paths,
                    anchor_paths=cached_anchor_paths,
                    pipeline_version=QUICK_PIPELINE_VERSION,
                    candidate_summary=(cached_audit or {}).get("candidate_summary") or raw,
                    final_summary=raw,
                    reduction_layers=0,
                    group_counts=[],
                    provider_calls={
                        "aggregate_total": 0,
                        "final": 0,
                        "selector": 0,
                    },
                    cache={"hit": True, "revalidated": False},
                    timings={
                        "total_ms": int(round((time.time() - started_at) * 1000)),
                    },
                    errors=errors,
                )
                cache.put_ok(
                    kind=target_kind,
                    path=target_path,
                    mode="quick",
                    manifest_hash=manifest_hash_value,
                    inputs_hash=cached_inputs_hash,
                    inventory_hash=inventory_hash,
                    model=model,
                    prompt_fp=prompt_fp,
                    summary_text=raw,
                    file_count=len(in_scope),
                    skipped_count=skipped_count,
                    reduction_layers=0,
                    batch_count=0,
                    group_counts=None,
                    inventory_count=len(inventory_items),
                    scope_label=SCOPE_LABEL,
                    scope_definition_version=SCOPE_DEFINITION_VERSION,
                    audit_json=serialize_audit_artifact(audit_payload),
                )
                return raw

        anchor_max_bytes = int(app_config.get("summary_anchor_max_bytes", 64 * 1024) or 64 * 1024)
        anchor_items = _collect_anchor_blocks(
            direct_metas,
            folder_prefix,
            file_processor,
            max_bytes=anchor_max_bytes,
        )
        anchor_paths = [path for path, _ in anchor_items]
        anchor_blocks = format_anchor_blocks(anchor_items)

        tree_snapshot = build_tree_snapshot(
            [folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix) for meta in in_scope],
            max_depth=2,
        )
        inventory_list_text = build_inventory_list(inventory_items)

        direct_file_map = {
            folder_rel_path(posix(meta.rel_path.as_posix()), folder_prefix): meta
            for meta in direct_metas
        }
        child_stats: list[ChildStat] = []
        for item in inventory_items:
            if item.endswith("/"):
                child_stats.append(ChildStat(path=item, kind="folder", size_bytes=None))
                continue
            meta = direct_file_map.get(item)
            size_bytes = int(meta.size_bytes) if meta is not None else None
            child_stats.append(ChildStat(path=item, kind="file", size_bytes=size_bytes))
        stats_text = build_child_stats(child_stats)

        max_depth = int(app_config.get("quick_summary_max_depth", 2) or 2)
        quick_file_kind = str(app_config.get("quick_summary_file_kind", "short") or "short")
        quick_file_kind = "short" if quick_file_kind.lower().startswith("short") else "long"

        auto_selected = _auto_pick_inventory_items(inventory_items, app_config=app_config)

        if auto_selected:
            selected = list(auto_selected)
        elif (
            cached
            and cached.get("status") == "ok"
            and cached.get("manifest_hash") == manifest_hash_value
            and cached.get("inventory_hash") == inventory_hash
            and cached_selected_paths
            and cached_pipeline_version == QUICK_PIPELINE_VERSION
            and cached_runtime_fingerprint == runtime_fingerprint
        ):
            selected = [
                item
                for item in cached_selected_paths
                if item in inventory_items
            ]
        elif inventory_items:
            n_picks = _quick_pick_count(len(inventory_items), default=8, max_picks=20)
            try:
                selector_started = time.time()
                selector_prompt = build_quick_selector_prompt(
                    target_kind,
                    repo_name=repo_name,
                    folder_rel=None if target_kind == "repo" else scope_rel,
                    n_picks=n_picks,
                    inventory_list=inventory_list_text,
                    child_stats=stats_text,
                )
                selector_output = fetch_ai_text_summary(
                    prompt_text=selector_prompt,
                    app_config=app_config,
                    token_counter=file_processor.count_tokens,
                    max_output_tokens_override=resolve_max_output_tokens(
                        app_config, "selector", model_name=model
                    ),
                    log_context={
                        "target_kind": target_kind,
                        "target_path": target_path,
                        "prompt_fp": prompt_fp,
                        "phase": "selector",
                    },
                )
                selector_calls = 1
                phase_timings_ms["selector_ms"] = int(round((time.time() - selector_started) * 1000))
                selected = parse_selector_output(selector_output, inventory_items)
            except Exception as exc:
                errors.append(f"selector: {exc}")
                selected = []
        else:
            selected = []

        if not selected and inventory_items:
            selected = _heuristic_picks(
                inventory_items,
                n_picks=_quick_pick_count(len(inventory_items), default=6, max_picks=12),
            )

        if target_kind == "repo":
            selected = _ensure_repo_folder_pick(selected, inventory_items)
        else:
            folder_items = [item for item in inventory_items if item.endswith("/")]
            minimum_folders = min(len(folder_items), 6 if len(folder_items) >= 6 else len(folder_items))
            selected = _ensure_folder_breadth(selected, inventory_items, minimum=minimum_folders)
        selected = _ensure_signal_file_coverage(selected, inventory_items, max_extra=2)
        nested_signal_items = (
            _collect_nested_signal_file_items(
                selected=selected,
                in_scope=in_scope,
                folder_prefix=folder_prefix,
                max_total=4,
                max_per_folder=3,
            )
            if depth + 1 <= max_depth
            else []
        )

        picks = list(dict.fromkeys(anchor_paths + selected))
        if not picks and inventory_items:
            picks = _heuristic_picks(
                inventory_items,
                n_picks=_quick_pick_count(len(inventory_items), default=6, max_picks=12),
            )
        if picks and len(picks) > 25:
            picks = picks[:25]

        child_folder_set = {name for name in children.keys()}
        selected_files: list[tuple[str, str]] = []
        selected_file_rels: set[str] = set()
        for item in picks:
            if item.endswith("/"):
                folder_name = item.rstrip("/")
                if folder_name not in child_folder_set:
                    continue
                if depth + 1 > max_depth:
                    evidence_items.append((item, "Quick summary skipped due to depth limit."))
                    continue
                summary_text = _summarize_quick(
                    target_kind="folder",
                    target_path=str(Path(target_path) / folder_name),
                    root_abs=root_abs,
                    scan_files=scan_files,
                    file_processor=file_processor,
                    app_config=app_config,
                    depth=depth + 1,
                    cache=cache,
                    scope_source=scope_source,
                )
                evidence_items.append((item, summary_text))
                continue
            meta = direct_file_map.get(item)
            if not meta:
                continue
            selected_files.append((str(meta.abs_path), item))
            selected_file_rels.add(item)

        for rel_local, meta in nested_signal_items:
            if rel_local in selected_file_rels:
                continue
            selected_files.append((str(meta.abs_path), rel_local))
            selected_file_rels.add(rel_local)

        if selected_files:
            file_summary_started = time.time()
            summaries, _summary_errors = ensure_summaries(
                file_paths=[abs_path for abs_path, _ in selected_files],
                kind=quick_file_kind,
                file_processor=file_processor,
                app_config=app_config,
                root=root_abs,
                strict_required=False,
            )
            phase_timings_ms["file_summaries_ms"] = int(round((time.time() - file_summary_started) * 1000))
            missing_selected_files: list[str] = []
            for abs_path, rel_local in selected_files:
                summary_text = summaries.get(abs_path)
                if summary_text is None:
                    missing_selected_files.append(rel_local)
                else:
                    evidence_items.append((rel_local, summary_text))
            if missing_selected_files:
                errors.append(
                    "selected evidence summaries unavailable: " + ", ".join(sorted(missing_selected_files))
                )
                if not evidence_items:
                    evidence_items = [
                        (
                            "PARTIAL",
                            "Quick summary evidence degraded because selected file summaries were unavailable: "
                            + ", ".join(sorted(missing_selected_files)),
                        )
                    ]

        if not evidence_items:
            evidence_items = [("EMPTY", "No quick evidence selected.")]

        inputs_hash = compute_inputs_hash(evidence_items)
        if (
            cache_enabled
            and
            cached
            and cached.get("status") == "ok"
            and cached.get("manifest_hash") == manifest_hash_value
            and cached.get("inventory_hash") == inventory_hash
            and cached.get("inputs_hash") == inputs_hash
            and cached_pipeline_version == QUICK_PIPELINE_VERSION
            and cached_runtime_fingerprint == runtime_fingerprint
        ):
            cached = cache.get(
                kind=target_kind,
                path=target_path,
                model=model,
                prompt_fp=prompt_fp,
                mode="quick",
                manifest_hash=manifest_hash_value,
                inventory_hash=inventory_hash,
                inputs_hash=inputs_hash,
            )
            cached_audit = parse_audit_artifact(cached.get("audit_json")) if cached else None
            raw = cached.get("summary_text") if cached else None
            if isinstance(raw, str) and raw:
                raw, sanitized_invalid_paths = sanitize_scoped_summary_paths(
                    raw,
                    inventory_items=inventory_items,
                    manifest_paths=manifest_paths,
                    scope_path_prefix="" if scope_rel in {"", "."} else scope_rel,
                    repo_root_abs=root_abs,
                    allow_existing_repo_paths_outside_scope=_allow_repo_external_path_mentions(target_kind),
                    replacement_style="redact",
                )
                if sanitized_invalid_paths:
                    errors.append(
                        "sanitized invalid cached path references: " + ", ".join(sorted(sanitized_invalid_paths))
                    )
                raw = sanitize_template_placeholder_literals(raw)
                audit_payload = build_audit_artifact(
                    mode="quick",
                    target_kind=target_kind,
                    target_path=target_path,
                    scope_source=scope_source,
                    manifest_hash=manifest_hash_value,
                    inputs_hash=inputs_hash,
                    inventory_hash=inventory_hash,
                    runtime_fingerprint=runtime_fingerprint,
                    file_count=len(in_scope),
                    skipped_count=skipped_count,
                    inventory_count=len(inventory_items),
                    manifest_paths=manifest_paths,
                    inventory_items=inventory_items,
                    selected_evidence_paths=[path for path, _ in evidence_items],
                    anchor_paths=anchor_paths,
                    pipeline_version=QUICK_PIPELINE_VERSION,
                    candidate_summary=(cached_audit or {}).get("candidate_summary") or raw,
                    final_summary=raw,
                    reduction_layers=0,
                    group_counts=[],
                    provider_calls={
                        "aggregate_total": selector_calls,
                        "final": 0,
                        "selector": selector_calls,
                    },
                    cache={"hit": True, "revalidated": False},
                    timings={
                        **phase_timings_ms,
                        "total_ms": int(round((time.time() - started_at) * 1000)),
                    },
                    errors=errors,
                )
                if cache_enabled:
                    cache.put_ok(
                        kind=target_kind,
                        path=target_path,
                        mode="quick",
                        manifest_hash=manifest_hash_value,
                        inputs_hash=inputs_hash,
                        inventory_hash=inventory_hash,
                        model=model,
                        prompt_fp=prompt_fp,
                        summary_text=raw,
                        file_count=len(in_scope),
                        skipped_count=skipped_count,
                        reduction_layers=0,
                        batch_count=0,
                        group_counts=None,
                        inventory_count=len(inventory_items),
                        scope_label=SCOPE_LABEL,
                        scope_definition_version=SCOPE_DEFINITION_VERSION,
                        audit_json=serialize_audit_artifact(audit_payload),
                    )
                return raw

        evidence_blocks = format_evidence_blocks(evidence_items)
        if target_kind == "repo":
            prompt_text = build_repo_summary_prompt(
                "quick",
                profile=profile,
                repo_name=repo_name,
                manifest_id=inventory_hash,
                scope_label=SCOPE_LABEL,
                scope_def_version=SCOPE_DEFINITION_VERSION,
                inventory_list=inventory_list_text,
                tree_snapshot=tree_snapshot,
                anchor_blocks=anchor_blocks,
                evidence_blocks=evidence_blocks,
            )
        else:
            prompt_text = build_folder_summary_prompt(
                "quick",
                profile=profile,
                repo_name=repo_name,
                folder_rel=scope_rel,
                manifest_id=inventory_hash,
                scope_label=SCOPE_LABEL,
                scope_def_version=SCOPE_DEFINITION_VERSION,
                inventory_list=inventory_list_text,
                tree_snapshot=tree_snapshot,
                anchor_blocks=anchor_blocks,
                evidence_blocks=evidence_blocks,
            )

        summary_started = time.time()
        summary_text = fetch_ai_text_summary(
            prompt_text=prompt_text,
            app_config=app_config,
            token_counter=file_processor.count_tokens,
            log_context={
                "target_kind": target_kind,
                "target_path": target_path,
                "prompt_fp": prompt_fp,
                "phase": "quick",
            },
        )
        if target_kind == "folder":
            summary_text = _normalize_quick_summary_paths(
                summary_text,
                scope_rel=scope_rel,
                inventory_items=inventory_items,
                in_scope=in_scope,
                folder_prefix=folder_prefix,
            )
        summary_text = augment_change_pointer_section(
            summary_text,
            candidate_paths=manifest_paths,
            headings=("## Where to change behavior first",),
        )
        summary_text, sanitized_invalid_paths = sanitize_scoped_summary_paths(
            summary_text,
            inventory_items=inventory_items,
            manifest_paths=manifest_paths,
            scope_path_prefix="" if scope_rel in {"", "."} else scope_rel,
            repo_root_abs=root_abs,
            allow_existing_repo_paths_outside_scope=_allow_repo_external_path_mentions(target_kind),
            replacement_style="redact",
        )
        if sanitized_invalid_paths:
            errors.append("sanitized invalid path references: " + ", ".join(sorted(sanitized_invalid_paths)))
        summary_text = sanitize_template_placeholder_literals(summary_text)
        phase_timings_ms["quick_summary_ms"] = int(round((time.time() - summary_started) * 1000))

        audit_payload = build_audit_artifact(
            mode="quick",
            target_kind=target_kind,
            target_path=target_path,
            scope_source=scope_source,
            manifest_hash=manifest_hash_value,
            inputs_hash=inputs_hash,
            inventory_hash=inventory_hash,
            runtime_fingerprint=runtime_fingerprint,
            file_count=len(in_scope),
            skipped_count=skipped_count,
            inventory_count=len(inventory_items),
            manifest_paths=manifest_paths,
            inventory_items=inventory_items,
            selected_evidence_paths=[path for path, _ in evidence_items],
            anchor_paths=anchor_paths,
            pipeline_version=QUICK_PIPELINE_VERSION,
            candidate_summary=summary_text,
            final_summary=summary_text,
            reduction_layers=0,
            group_counts=[],
            provider_calls={
                "aggregate_total": selector_calls + 1,
                "final": 1,
                "selector": selector_calls,
            },
            cache={"hit": False, "revalidated": False},
            timings={
                **phase_timings_ms,
                "total_ms": int(round((time.time() - started_at) * 1000)),
            },
            errors=errors,
        )
        if cache_enabled:
            cache.put_ok(
                kind=target_kind,
                path=target_path,
                mode="quick",
                manifest_hash=manifest_hash_value,
                inputs_hash=inputs_hash,
                inventory_hash=inventory_hash,
                model=model,
                prompt_fp=prompt_fp,
                summary_text=summary_text,
                file_count=len(in_scope),
                skipped_count=skipped_count,
                reduction_layers=0,
                batch_count=0,
                group_counts=None,
                inventory_count=len(inventory_items),
                scope_label=SCOPE_LABEL,
                scope_definition_version=SCOPE_DEFINITION_VERSION,
                audit_json=serialize_audit_artifact(audit_payload),
            )
        return summary_text
    except Exception as exc:
        try:
            inputs_hash = compute_inputs_hash(evidence_items) if evidence_items else None
            audit_payload = build_audit_artifact(
                mode="quick",
                target_kind=target_kind,
                target_path=target_path,
                scope_source=scope_source,
                manifest_hash=manifest_hash_value or None,
                inputs_hash=inputs_hash,
                inventory_hash=inventory_hash or None,
                runtime_fingerprint=runtime_fingerprint,
                file_count=len(in_scope) if "in_scope" in locals() else 0,
                skipped_count=skipped_count,
                inventory_count=len(inventory_items),
                manifest_paths=manifest_paths if "manifest_paths" in locals() else [],
                inventory_items=inventory_items,
                selected_evidence_paths=[path for path, _ in evidence_items],
                anchor_paths=anchor_paths,
                pipeline_version=QUICK_PIPELINE_VERSION,
                candidate_summary=None,
                final_summary=None,
                reduction_layers=0,
                group_counts=[],
                provider_calls={},
                cache={"hit": False, "revalidated": False},
                timings={
                    **phase_timings_ms,
                    "total_ms": int(round((time.time() - started_at) * 1000)),
                },
                errors=errors + [str(exc)],
            )
            if cache_enabled:
                cache.put_error(
                    kind=target_kind,
                    path=target_path,
                    mode="quick",
                    manifest_hash=manifest_hash_value or "",
                    inputs_hash=inputs_hash,
                    inventory_hash=inventory_hash or None,
                    model=model,
                    prompt_fp=prompt_fp,
                    file_count=len(in_scope) if "in_scope" in locals() else 0,
                    skipped_count=skipped_count,
                    reduction_layers=0,
                    batch_count=0,
                    group_counts=None,
                    inventory_count=len(inventory_items),
                    scope_label=SCOPE_LABEL,
                    scope_definition_version=SCOPE_DEFINITION_VERSION,
                    audit_json=serialize_audit_artifact(audit_payload),
                )
        except Exception:
            pass
        raise


def ensure_folder_summary(
    *,
    folder_abs: str,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    _depth: int = 0,
    _cache: object | None = None,
    scope_source: str = "caller_scan_files",
) -> str:
    cache_local = (
        _cache
        if isinstance(_cache, AggregateCache)
        else AggregateCache(resolve_aggregate_cache_db_path(app_config))
    )
    try:
        return _summarize_quick(
            target_kind="folder",
            target_path=folder_abs,
            root_abs=root_abs,
            scan_files=scan_files,
            file_processor=file_processor,
            app_config=app_config,
            depth=_depth,
            cache=cache_local,
            scope_source=scope_source,
        )
    except TruncationError:
        raise RuntimeError("Quick summary truncated; reduce evidence selection.")
    finally:
        if _cache is None:
            try:
                cache_local.close()
            except Exception:
                pass


def ensure_repo_summary(
    *,
    root_abs: str,
    scan_files: list[FileMeta],
    file_processor: FileProcessor,
    app_config: dict,
    _cache: object | None = None,
    scope_source: str = "caller_scan_files",
) -> str:
    cache_local = (
        _cache
        if isinstance(_cache, AggregateCache)
        else AggregateCache(resolve_aggregate_cache_db_path(app_config))
    )
    try:
        return _summarize_quick(
            target_kind="repo",
            target_path=root_abs,
            root_abs=root_abs,
            scan_files=scan_files,
            file_processor=file_processor,
            app_config=app_config,
            depth=0,
            cache=cache_local,
            scope_source=scope_source,
        )
    except TruncationError:
        raise RuntimeError("Quick summary truncated; reduce evidence selection.")
    finally:
        if _cache is None:
            try:
                cache_local.close()
            except Exception:
                pass


__all__ = ["ensure_folder_summary", "ensure_repo_summary"]
