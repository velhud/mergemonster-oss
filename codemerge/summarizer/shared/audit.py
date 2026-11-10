from __future__ import annotations

import json
from typing import Any, Mapping


def serialize_audit_artifact(payload: Mapping[str, Any] | None) -> str | None:
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def parse_audit_artifact(raw: str | None) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        loaded = json.loads(raw)
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def build_audit_artifact(
    *,
    mode: str,
    target_kind: str,
    target_path: str,
    scope_source: str,
    manifest_hash: str | None,
    inputs_hash: str | None,
    inventory_hash: str | None,
    runtime_fingerprint: str | None,
    file_count: int,
    skipped_count: int,
    inventory_count: int,
    manifest_paths: list[str] | None = None,
    skipped_items: list[Mapping[str, Any]] | None = None,
    inventory_items: list[str] | None = None,
    selected_evidence_paths: list[str],
    anchor_paths: list[str] | None = None,
    pipeline_version: int | None = None,
    candidate_summary: str | None = None,
    final_summary: str | None = None,
    reduction_layers: int = 0,
    group_counts: list[int] | None = None,
    reduction_plan: list[Mapping[str, Any]] | None = None,
    provider_calls: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    timings: Mapping[str, Any] | None = None,
    validation: Mapping[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "mode": str(mode or ""),
        "target_kind": str(target_kind or ""),
        "target_path": str(target_path or ""),
        "scope_source": str(scope_source or "caller_scan_files"),
        "manifest_hash": manifest_hash,
        "inputs_hash": inputs_hash,
        "inventory_hash": inventory_hash,
        "runtime_fingerprint": runtime_fingerprint,
        "file_count": int(file_count or 0),
        "skipped_count": int(skipped_count or 0),
        "inventory_count": int(inventory_count or 0),
        "manifest_paths": list(manifest_paths or []),
        "skipped_items": [dict(item) for item in (skipped_items or []) if isinstance(item, Mapping)],
        "inventory_items": list(inventory_items or []),
        "selected_evidence_paths": list(selected_evidence_paths or []),
        "anchor_paths": list(anchor_paths or []),
        "pipeline_version": int(pipeline_version or 0) if pipeline_version is not None else None,
        "candidate_summary": candidate_summary,
        "final_summary": final_summary,
        "reduction_layers": int(reduction_layers or 0),
        "group_counts": list(group_counts or []),
        "reduction_plan": [dict(item) for item in (reduction_plan or []) if isinstance(item, Mapping)],
        "provider_calls": dict(provider_calls or {}),
        "cache": dict(cache or {}),
        "timings": dict(timings or {}),
        "validation": dict(validation or {}),
        "errors": list(errors or []),
    }


__all__ = [
    "build_audit_artifact",
    "parse_audit_artifact",
    "serialize_audit_artifact",
]
