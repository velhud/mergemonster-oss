

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional
import inspect
import json
import os
from ..filemerger import FileProcessor
from ..filemerger.exclude_utils import (
    is_abs_excluded,
    norm_path,
    split_excluded_dirs,
    split_excluded_files,
)
from ..llm.embed_types import DEFAULT_OPENAI_EMBED_MODEL
from ..llm.config import DEFAULT_GROQ_VERIFIER_MODEL
from ..llm.clients import groq_available
from ..llm.prune import (
    interpret_goal_with_llm as llm_interpret_goal,
    select_spans_with_llm as llm_select_spans,
    run_pruning_agent_exchange,
)
from ..logger import LOG_DIR, get_logger, log_event, log_stage
from ..logging_ctx import set_event_writer, set_run_id
from ..prune.pruner import (
    FilePatch,
    apply_patch,
    build_prune_plan,
    delete_defs,
    write_pruned_merge,
)
from ..prune.retrieval import DEFAULT_GROQ_EMBED_MODEL, expand_neighbors, rank_candidates
from ..prune.symbol_index import (
    _index_stale,
    build_symbol_index,
    load_symbol_index,
    save_symbol_index,
)
from ..prune.verifier import final_sanity_pass, validate_and_repair

logger = get_logger(__name__)

# ------------------------- internal helpers -------------------------

@dataclass
class PruneStats:


    written: str
    kept_defs: int
    kept_files: int
    tokens: int
    overflow: str | None = None
    overflow_defs: int = 0
    overflow_files: int = 0
    overflow_tokens: int = 0


def _thin_index(index: Dict[str, Dict], *, max_files: int = 150, max_defs_per_file: int = 3) -> list[dict]:
    # Prefer larger, more central files first by tokens (fallback to size)
    items = []
    for path, info in index.items():
        defs = info.get("defs", []) or []
        # Extract only name, kind, and first doc line
        short_defs = []
        for d in defs[: max_defs_per_file]:
            doc = d.get("doc") or ""
            doc_first = doc.splitlines()[0] if isinstance(doc, str) and doc else ""
            short_defs.append({
                "name": d.get("name"),
                "kind": d.get("kind"),
                "doc_first": doc_first,
            })
        items.append({
            "path": path,
            "defs": short_defs,
        })
    items.sort(key=lambda it: (index.get(it["path"], {}).get("tokens") or 0, index.get(it["path"], {}).get("size") or 0), reverse=True)
    return items[: max_files]


def interpret_goal_with_llm(
    goal: str,
    thin_index: list[dict],
    *,
    client: Optional[object],
    cfg: Dict,
) -> dict:
    with log_stage(logger, "interpret_goal"):
        return llm_interpret_goal(goal, thin_index, client=client, cfg=cfg)


def _prefilter_spans_for_llm(goal: str, index: Dict, cfg: Dict) -> list[dict]:
    try:
        # Local import to avoid widening module imports at top
        from ..prune.retrieval import rank_candidates
    except Exception:
        return []
    cands = rank_candidates(
        goal,
        index,
        top_k=int(cfg.get("llm_prefilter_top_k", 800) or 800),
        use_embeddings=False,
        retrieval_backend="embeddings",
        ranking_mode="keywords",
        embedding_model=cfg.get("groq_embedding_model", DEFAULT_GROQ_EMBED_MODEL),
        seed_files=[],
        seed_defs=[],
        exclude_globs=[],
        exclude_exts=[],
        allowed_paths=None,
        exclude_tests=cfg.get("prune_exclude_tests", True),
        downrank_noise=True,
        noise_dirs=cfg.get("prune_noise_dirs", ["tools", "scripts", "examples", "docs"]),
    )
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for c in cands:
        if c.kind not in ("def", "class") or not c.qualname:
            continue
        key = (c.path, c.qualname)
        if key in seen:
            continue
        info = index.get(c.path, {})
        doc_first = ""
        for d in info.get("defs", []) or []:
            if d.get("qualname") == c.qualname:
                doc = d.get("doc") or ""
                doc_first = doc.splitlines()[0] if isinstance(doc, str) and doc else ""
                break
        items.append(
            {
                "file_path": c.path,
                "qualname": c.qualname,
                "kind": c.kind,
                "doc_first": doc_first,
            }
        )
        seen.add(key)
    return items


def select_spans_with_llm(
    goal: str,
    index: Dict,
    *,
    client: Optional[object],
    cfg: Dict,
) -> list[dict]:
    options = _prefilter_spans_for_llm(goal, index, cfg)
    keep = llm_select_spans(goal=goal, options=options, client=client, cfg=cfg)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for it in keep:
        p = it.get("file_path")
        q = it.get("qualname")
        if not p or not q or (p, q) in seen:
            continue
        info = index.get(p, {})
        found = None
        for d in info.get("defs", []) or []:
            if d.get("qualname") == q:
                found = d
                break
        if found:
            out.append({"file_path": p, "qualname": q})
            seen.add((p, q))
    return out


def run_pruning_agent(
    goal: str,
    index: Dict,
    *,
    client: Optional[object] = None,
    config: Dict | None = None,
    dry_run: bool = False,
    allowed_paths: Optional[set[str]] = None,
) -> Dict | Dict[str, object]:

    logger.info("run_pruning_agent start")
    if allowed_paths is not None:
        allowed_paths = {str(Path(p).resolve()) for p in allowed_paths}

    cfg = config or {}

    # Track executed deletions for a robust fallback if the model doesn't emit a plan
    executed_by_file: Dict[str, set[str]] = {}
    patches: list[FilePatch] = []

    def _handle_tool_call(call: dict) -> dict:
        name = call.get("name")
        args_raw = call.get("args_raw")
        try:
            if isinstance(args_raw, str):
                args = json.loads(args_raw) if args_raw else {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
        except Exception:
            args = {}
        if name == "delete_defs":
            file_path = args.get("file_path", "")
            defs = args.get("defs", []) or []
            if allowed_paths is not None and str(Path(file_path).resolve()) not in allowed_paths:
                return {"status": "disallowed"}
            try:
                removed, patch = delete_defs(file_path, defs)
                log_event(
                    logger,
                    "delete_defs",
                    status="ok",
                    file_path=file_path,
                    defs=defs,
                    removed=removed,
                )
                if patch:
                    patches.append(patch)
            except Exception as exc:
                log_event(
                    logger,
                    "delete_defs",
                    status="error",
                    file_path=file_path,
                    defs=defs,
                    error=str(exc),
                )
                raise
            if file_path:
                s = executed_by_file.setdefault(file_path, set())
                for d in removed:
                    s.add(d)
            return {"status": "ok"}
        return {"status": "ignored"}

    resp = run_pruning_agent_exchange(
        goal=goal,
        index=index,
        client=client,
        cfg=cfg,
        handle_tool_call=_handle_tool_call,
    )

    # Prefer the model's structured plan; fall back to a plan synthesized from executed tool calls.
    raw_plan = getattr(resp, "output_text", "") or ""
    plan: Dict
    plan_valid = False
    try:
        parsed = json.loads(raw_plan) if raw_plan else {}
        if isinstance(parsed, dict) and "deletions" in parsed:
            plan = parsed
            plan_valid = True
        else:
            raise ValueError("missing 'deletions' in parsed plan")
    except Exception:
        synthesized = [
            {"file_path": fp, "defs": sorted(list(defs))}
            for fp, defs in executed_by_file.items()
        ]
        plan = {"deletions": synthesized} if synthesized else {"deletions": []}
        patches = []  # discard when validation fails

    log_event(logger, "agent_plan_parsed", valid=plan_valid, deletions=len(plan.get("deletions", [])))

    if dry_run:
        return {"plan": plan, "patches": patches if plan_valid else []}

    # If we have a valid plan but no patches (e.g., the model emitted a plan but did not call tools),
    # execute deletions directly from the plan to generate patches.
    if plan_valid and not patches:
        for entry in plan.get("deletions", []):
            file_path = entry.get("file_path", "")
            defs = entry.get("defs", []) or []
            try:
                removed, patch = delete_defs(file_path, defs)
                logger.info(
                    json.dumps(
                        {
                            "event": "delete_defs_from_plan",
                            "status": "ok",
                            "file_path": file_path,
                            "defs": defs,
                            "removed": removed,
                        }
                    )
                )
                if patch:
                    patches.append(patch)
            except Exception as exc:
                logger.error(
                    json.dumps(
                        {
                            "event": "delete_defs_from_plan",
                            "status": "error",
                            "file_path": file_path,
                            "defs": defs,
                            "error": str(exc),
                        }
                    )
                )
                raise

    # Apply patches only when we have a validated plan
    if plan_valid:
        for p in patches:
            apply_patch(p)

    log_event(
        logger,
        "final_pruning_plan",
        valid=plan_valid,
        deletions=len(plan.get("deletions", [])),
    )

    return plan


def prune_by_goal(
    goal: str,
    root: str,
    out_path: str,
    config: Dict | None = None,
    progress: Optional[Callable[[str], None]] = None,
    allowed_paths: Optional[list[str]] = None,
) -> Dict:

    import uuid
    from datetime import datetime
    run_id = uuid.uuid4().hex[:10]
    set_run_id(run_id)
    log_file_path = (
        LOG_DIR / f"log_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{run_id}.jsonl"
    )
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_file_path.open("w", encoding="utf-8")
    set_event_writer(log_file)
    cfg = dict(config or {})
    if "prune_goal_llm_enabled" not in cfg:
        cfg["prune_goal_llm_enabled"] = groq_available()
    log_event(
        logger,
        "prune_run_start",
        goal=str(goal),
        settings={
            "ranking_mode": cfg.get("prune_ranking_mode"),
            "engine": cfg.get("prune_engine"),
            "neighbors": cfg.get("prune_neighbors_mode"),
            "mode": cfg.get("prune_mode"),
            "token_budget": cfg.get("prune_token_budget"),
            "top_k": cfg.get("prune_top_k"),
            "enable_embeddings": cfg.get("enable_embeddings"),
            "embedding_provider": cfg.get("embedding_provider"),
            "retrieval_backend": cfg.get("retrieval_backend"),
            "keep_neighbors": cfg.get("prune_keep_neighbors"),
            "exclude_tests": cfg.get("prune_exclude_tests"),
            "max_repairs": cfg.get("prune_max_repairs"),
            "strict_mode": cfg.get("groq_strict_mode"),
        },
        subset_mode=bool(allowed_paths),
        allowed_paths=len(allowed_paths or []),
    )

    fp = FileProcessor()
    try:
        if isinstance(cfg.get("custom_ext_map"), dict):
            fp.set_custom_ext_map(cfg.get("custom_ext_map", {}))
        excluded_dirs_raw = cfg.get("excluded_dirs", [])
        if isinstance(excluded_dirs_raw, list):
            fp.set_excluded_dirs({str(x) for x in excluded_dirs_raw if isinstance(x, str) and x.strip()})
        excluded_files_raw = cfg.get("excluded_files", [])
        if isinstance(excluded_files_raw, list):
            fp.set_excluded_files({str(x) for x in excluded_files_raw if isinstance(x, str) and x.strip()})
        # Noise presets mirror UI scan defaults
        if bool(cfg.get("exclude_node_modules", True)):
            fp.excluded_dirs.add("node_modules")
        if bool(cfg.get("exclude_ios_noise", True)):
            fp.excluded_dirs |= {
                "DerivedData",
                "Pods",
                "Carthage",
                ".build",
                ".swiftpm",
                "xcuserdata",
                "xcshareddata",
            }
        if bool(cfg.get("exclude_python_noise", False)):
            fp.excluded_dirs |= {
                ".venv",
                "venv",
                "__pycache__",
                ".mypy_cache",
                ".pytest_cache",
                "build",
                "dist",
                ".tox",
            }
        if bool(cfg.get("exclude_java_gradle_noise", False)):
            fp.excluded_dirs |= {".gradle", "build", "out", "target"}
        if bool(cfg.get("exclude_web_build_noise", False)):
            fp.excluded_dirs |= {"dist", "build", ".next", ".nuxt", ".angular", "coverage"}
    except Exception:
        # Fall back to default processor behavior
        pass
    subset_mode = bool(allowed_paths)
    if subset_mode:
        logger.info("building scan context from allowed_paths=%d", len(allowed_paths or []))
        # Ensure UI/config excludes still apply even in subset mode.
        filtered: list[str] = []
        excluded_dirs_rel, excluded_dirs_abs = split_excluded_dirs(getattr(fp, "excluded_dirs", set()))
        excluded_files_name, excluded_files_rel, excluded_files_abs = split_excluded_files(
            getattr(fp, "excluded_files", set())
        )
        try:
            root_resolved = Path(root).resolve()
        except Exception:
            root_resolved = Path(root)
        for entry in (allowed_paths or []):
            try:
                p = Path(entry).resolve()
            except Exception:
                p = Path(entry)
            try:
                rel = p.relative_to(root_resolved)
            except Exception:
                rel = None
            if rel is not None:
                try:
                    name_norm = norm_path(rel.name)
                    if name_norm in excluded_files_name:
                        continue
                    abs_norm = norm_path(str(p))
                    if abs_norm in excluded_files_abs:
                        continue
                    rel_norm = norm_path(str(rel))
                    if rel_norm in excluded_files_rel:
                        continue
                except Exception:
                    pass
                try:
                    parts = list(rel.parts[:-1])
                    blocked = False
                    for idx in range(1, len(parts) + 1):
                        prefix = os.path.normpath(os.path.join(*parts[:idx]))
                        prefix_norm = norm_path(prefix)
                        if prefix_norm in excluded_dirs_rel:
                            blocked = True
                            break
                        abs_dir = str(Path(root) / prefix)
                        abs_norm = norm_path(abs_dir)
                        if excluded_dirs_abs and is_abs_excluded(abs_norm, excluded_dirs_abs):
                            blocked = True
                            break
                        if fp.is_virtual_env(abs_dir):
                            blocked = True
                            break
                    if blocked:
                        continue
                except Exception:
                    pass
            filtered.append(str(p))
        allowed_paths = filtered
        ctx = fp.scan_file_list(
            root,
            allowed_paths or [],
            sort_by=cfg.get("sort_by", "name"),
            sort_descending=cfg.get("sort_descending", False),
        )
    else:
        ctx = fp.scan_directory(
            directory=root,
            file_types=cfg.get("file_types", {}),
            include_all=cfg.get("include_all", False),
            sort_by=cfg.get("sort_by", "name"),
            sort_descending=cfg.get("sort_descending", False),
            ignore_binary=cfg.get("ignore_binary_files", False),
            max_file_size_mb=cfg.get("max_file_size_mb", 0.0),
        )

    index = {} if subset_mode else (load_symbol_index(root) or {})
    logger.info("scanned %d files", len(ctx.files))
    stale, _ = _index_stale(ctx, index)
    if stale:
        if progress:
            progress("Building symbol index...")
        logger.info("building symbol index")
        index = build_symbol_index(ctx, fp, index)
        if not subset_mode:
            save_symbol_index(root, index)
    else:
        logger.info("reusing symbol index")
        if progress:
            progress("Reusing symbol index")

    logger.info("ranking candidates")
    # Primary path: LLM span selection -> minimal dependency closure
    cands = []
    if cfg.get("prune_goal_llm_span_select", True):
        if progress:
            progress("Selecting spans with LLM...")
        try:
            picks = select_spans_with_llm(goal, index, client=None, cfg=cfg)
        except Exception as e:
            logger.warning("LLM span selection failed (%s); falling back to legacy ranking", e)
            picks = []
        # Restrict to UI universe if provided
        if picks and allowed_paths:
            try:
                ap = {str(Path(x).resolve()) for x in allowed_paths}
                before = len(picks)
                picks = [it for it in picks if str(Path(it["file_path"]).resolve()) in ap]
                logger.info("llm picks gated by UI: %d -> %d", before, len(picks))
            except Exception:
                # if resolution fails, proceed without gating to avoid hard failure
                pass
        if picks:
            from ..prune.retrieval import Candidate as _Cand
            for it in picks:
                p = it["file_path"]
                q = it["qualname"]
                d = next((dd for dd in (index.get(p, {}).get("defs") or []) if dd.get("qualname") == q), None)
                if not d:
                    continue
                cands.append(_Cand(p, d.get("kind", "def"), q, d.get("start"), d.get("end"), 1e6, "llm"))
            # minimal neighbor expansion (same_file/import/attr)
            ln_mode = cfg.get(
                "llm_neighbors_mode", cfg.get("prune_neighbors_mode", "same_file")
            )
            if cands and ln_mode != "none":
                    cands = _expand_neighbors_compat(
                        cands,
                        index,
                        max_neighbors=cfg.get("llm_neighbor_max", 60),
                        seed_paths={c.path for c in cands},
                        # dedicated neighbor policy for LLM path (defaults to minimal)
                        neighbors_mode=ln_mode,
                        allowed_paths=set(allowed_paths) if allowed_paths else None,
                    )
        if not cands:
            logger.info("LLM span selection produced no candidates; using legacy ranking fallback")
    if not cands:
        # Fallback path: previous goal→seeds→ranking
        seeds_files: list[str] = []
        seeds_defs: list[str] = []
        exclude_globs: list[str] = []
        exclude_exts: list[str] = []
        if cfg.get("prune_goal_llm_enabled", True):
            if progress:
                progress("Interpreting goal...")
            thin = _thin_index(index, max_files=150, max_defs_per_file=3)
            try:
                plan = interpret_goal_with_llm(goal, thin, client=None, cfg=cfg)
            except Exception as exc:
                logger.warning("Goal interpretation failed (%s); continuing without LLM seeds", exc)
                plan = {}
            seeds_files = plan.get("include_files", []) or []
            seeds_defs = plan.get("include_defs", []) or []
            exclude_globs = plan.get("exclude_globs", []) or []
            exclude_exts = [e.lstrip(".").lower() for e in (plan.get("exclude_exts", []) or [])]
            if not seeds_files and seeds_defs:
                file_map: set[str] = set()
                for p, info in index.items():
                    for d in info.get("defs", []) or []:
                        q = d.get("qualname") or ""
                        if q in seeds_defs or (("." in q) and q.split(".")[-1] in seeds_defs):
                            file_map.add(p)
                if file_map:
                    seeds_files = sorted(file_map)
            if allowed_paths:
                ap = {str(Path(x).resolve()) for x in allowed_paths}
                seeds_files = [p for p in seeds_files if str(Path(p).resolve()) in ap]
        openai_embed_model = cfg.get("openai_embedding_model", DEFAULT_OPENAI_EMBED_MODEL)
        try:
            openai_embed_dims = int(cfg.get("openai_embedding_dimensions", 0) or 0)
        except Exception:
            openai_embed_dims = 0

        cands = rank_candidates(
            goal,
            index,
            top_k=cfg.get("prune_top_k", 200),
            use_embeddings=cfg.get("enable_embeddings", True),
            embedding_provider=cfg.get("embedding_provider", "groq"),
            retrieval_backend=cfg.get("retrieval_backend", "embeddings"),
            ranking_mode=cfg.get("prune_ranking_mode", "hybrid"),
            embedding_model=cfg.get("groq_embedding_model", DEFAULT_GROQ_EMBED_MODEL),
            openai_embedding_model=openai_embed_model,
            openai_embedding_dimensions=openai_embed_dims,
            seed_files=seeds_files,
            seed_defs=seeds_defs,
            exclude_globs=exclude_globs,
            exclude_exts=exclude_exts,
            allowed_paths=set(allowed_paths) if allowed_paths else None,
            exclude_tests=cfg.get("prune_exclude_tests", True),
            downrank_noise=cfg.get("prune_downrank_noise", True),
            noise_dirs=cfg.get("prune_noise_dirs", ["tools", "scripts", "examples", "docs"]),
        )
        neighbors_mode = cfg.get("prune_neighbors_mode", "same_package")
        keep_neighbors = cfg.get("prune_keep_neighbors", True) and neighbors_mode != "none"
        if keep_neighbors:
            cap = cfg.get("prune_neighbor_max", 80)
            if neighbors_mode == "auto":
                cap = min(150, 20 + 10 * len(seeds_files) + 2 * len(seeds_defs))
            cands = _expand_neighbors_compat(
                cands,
                index,
                max_neighbors=cap,
                seed_paths=set(seeds_files) if seeds_files else None,
                neighbors_mode=neighbors_mode,
                allowed_paths=set(allowed_paths) if allowed_paths else None,
            )
    logger.info("ranked %d candidates", len(cands))

    if progress:
        progress("Planning...")
    logger.info("building prune plan")
    plan = build_prune_plan(
        goal,
        index,
        cands,
        fp,
        budget_tokens=cfg.get("prune_token_budget", 45_000),
        mode=cfg.get("prune_mode", "safe"),
        span_to_file_threshold=cfg.get("prune_span_to_file_threshold", 120),
        keep_class_for_method=cfg.get("prune_keep_class_for_method", False),
    )

    if progress:
        progress("Verifying...")
    logger.info("verifying plan")
    validate_kwargs = {
        "max_passes": cfg.get("prune_max_repairs", 3),
        "observer_rounds": 3 if cfg.get("prune_execute_workspace") is True else 0,
        "prune_goal_llm_span_select": cfg.get("prune_goal_llm_span_select", True),
        "repair_prefer_spans": cfg.get("prune_repair_prefer_spans", True),
        "repair_max_spans_per_module": cfg.get("prune_repair_max_spans_per_module", 50),
        "repair_budget_tokens": cfg.get("prune_repair_budget_tokens", 0),
    }
    if allowed_paths:
        validate_kwargs["allowed_paths"] = set(allowed_paths)
    sig = inspect.signature(validate_and_repair)
    filtered_kwargs = {}
    for key, value in validate_kwargs.items():
        if key not in sig.parameters:
            continue
        if value is None and sig.parameters[key].default is inspect._empty:
            continue
        filtered_kwargs[key] = value
    plan, verify_logs = validate_and_repair(
        plan,
        root,
        index,
        fp,
        **filtered_kwargs,
    )
    if progress:
        progress("AI sanity check...")
    logger.info("running final sanity pass")
    sanity_result, stub_suggestions = None, None
    if cfg.get("prune_goal_llm_enabled") or cfg.get("prune_goal_llm_span_select", True):
        sanity_result, stub_suggestions = final_sanity_pass(
            verify_logs,
            cfg.get("groq_verifier_model", DEFAULT_GROQ_VERIFIER_MODEL),
        )
    if sanity_result:
        if sanity_result.get("status") == "ISSUES":
            verify_logs.append("Sanity issues:")
            verify_logs.extend(sanity_result.get("issues", []))
        else:
            verify_logs.append("Sanity check: OK")
    if stub_suggestions:
        verify_logs.append("Stub suggestions requested by model:")
        verify_logs.append(json.dumps(stub_suggestions, indent=2))

    if progress:
        progress("Writing...")
    logger.info("writing pruned merge to %s", out_path)
    written, overflow, stats_a, stats_b = write_pruned_merge(
        plan,
        root,
        out_path,
        fp,
        include_tree=cfg.get("include_file_tree", True),
        enumerate_lines=cfg.get("enumerate_lines", False),
        remove_empty=cfg.get("remove_empty", False),
        budget_tokens=cfg.get("prune_token_budget", 45_000),
        core_budget_tokens=cfg.get("prune_core_budget_tokens", 0),
        mode=cfg.get("prune_mode", "safe"),
        log_entries=verify_logs,
    )

    stats = PruneStats(
        written=written,
        kept_defs=stats_a["kept_defs"],
        kept_files=stats_a["kept_files"],
        tokens=stats_a["tokens"],
        overflow=overflow,
        overflow_defs=stats_b["kept_defs"] if overflow else 0,
        overflow_files=stats_b["kept_files"] if overflow else 0,
        overflow_tokens=stats_b["tokens"] if overflow else 0,
    )
    result = stats.__dict__
    if stub_suggestions:
        result["stub_suggestions"] = stub_suggestions
    log_event(
        logger,
        "prune_run_end",
        success=True,
        main=written,
        overflow=overflow,
    )
    log_file.close()
    return result
def _expand_neighbors_compat(cands, index, **kwargs):
    try:
        return expand_neighbors(cands, index, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" in str(exc):
            return expand_neighbors(cands, index)
        raise
