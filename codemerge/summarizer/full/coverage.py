from __future__ import annotations

from codemerge.llm.summarize import aggregate_prompt_fingerprint, resolve_full_summary_model
from codemerge.summarizer.shared.aggregate_cache import AggregateCache, resolve_aggregate_cache_db_path
from codemerge.summarizer.shared.audit import parse_audit_artifact


def _plain_prompt_fp(app_config: dict, kind: str) -> str:
    version = int(app_config.get("summary_prompt_version", 1) or 1)
    return aggregate_prompt_fingerprint(kind, "full", version)


def _plain_model(app_config: dict) -> str:
    return resolve_full_summary_model(app_config)


def _current_full_runtime_contract(app_config: dict) -> tuple[int, str]:
    from codemerge.summarizer.full import orchestrator as full_orch

    return full_orch.FULL_PIPELINE_VERSION, full_orch._full_runtime_fingerprint(app_config)


def get_full_coverage_proof(
    *,
    target_kind: str,
    target_path: str,
    app_config: dict,
    manifest_hash: str | None = None,
) -> dict | None:
    cache = AggregateCache(resolve_aggregate_cache_db_path(app_config))
    try:
        level_norm = str(target_kind or "").strip().lower()
        prompt_fp = _plain_prompt_fp(app_config, level_norm)
        model = _plain_model(app_config)
        row = cache.get(
            kind=target_kind,
            path=target_path,
            model=model,
            prompt_fp=prompt_fp,
            mode="full",
            manifest_hash=manifest_hash,
        )
        if not row or row.get("status") != "ok":
            return None
        group_counts_raw = row.get("group_counts") if isinstance(row, dict) else None
        group_counts: list[int] = []
        if isinstance(group_counts_raw, str) and group_counts_raw.strip():
            for part in group_counts_raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    group_counts.append(int(part))
                except Exception:
                    continue
        audit = parse_audit_artifact(row.get("audit_json"))
        current_pipeline, current_runtime = _current_full_runtime_contract(app_config)
        try:
            cached_pipeline = int((audit or {}).get("pipeline_version") or 0)
        except Exception:
            cached_pipeline = 0
        cached_runtime = str((audit or {}).get("runtime_fingerprint") or "")
        if cached_pipeline != current_pipeline or cached_runtime != current_runtime:
            return None
        coverage = {
            "file_count": row.get("file_count"),
            "skipped_count": row.get("skipped_count"),
            "reduction_layers": row.get("reduction_layers"),
            "batch_count": row.get("batch_count"),
            "group_counts": group_counts,
            "inputs_hash": row.get("inputs_hash"),
            "inventory_hash": row.get("inventory_hash"),
            "inventory_count": row.get("inventory_count"),
            "scope": row.get("scope_label"),
            "scope_definition_version": row.get("scope_definition_version"),
        }
        if audit:
            coverage["manifest_paths"] = list(audit.get("manifest_paths") or [])
            coverage["skipped_items"] = list(audit.get("skipped_items") or [])
            coverage["inventory_items"] = list(audit.get("inventory_items") or [])
            coverage["reduction_plan"] = list(audit.get("reduction_plan") or [])
            coverage["validation"] = dict(audit.get("validation") or {})
        return {
            "target_kind": target_kind,
            "target_path": target_path,
            "manifest_hash": row.get("manifest_hash"),
            "coverage": coverage,
            "audit": audit,
        }
    finally:
        try:
            cache.close()
        except Exception:
            pass


__all__ = ["get_full_coverage_proof"]
