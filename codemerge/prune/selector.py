from __future__ import annotations
from typing import Dict, List, Optional
from ..llm.prune import select_spans_with_llm as llm_select_spans


def _prefilter(index: Dict, top_k: int, exclude_tests: bool, noise_dirs: list[str]) -> List[dict]:
    """Cheap lexical prefilter to keep prompt small."""
    from .retrieval import rank_candidates, DEFAULT_GROQ_EMBED_MODEL

    cands = rank_candidates(
        goal="",  # lexical-only, we rank w.r.t. names/docs later in the LLM
        index=index,
        top_k=int(top_k),
        use_embeddings=False,
        retrieval_backend="embeddings",
        ranking_mode="keywords",
        embedding_model=DEFAULT_GROQ_EMBED_MODEL,
        seed_files=[],
        seed_defs=[],
        exclude_globs=[],
        exclude_exts=[],
        allowed_paths=None,
        exclude_tests=exclude_tests,
        downrank_noise=True,
        noise_dirs=noise_dirs,
    )
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for c in cands:
        if c.kind not in ("def", "class") or not c.qualname:
            continue
        k = (c.path, c.qualname)
        if k in seen:
            continue
        info = index.get(c.path, {})
        doc_first = ""
        for d in info.get("defs", []) or []:
            if d.get("qualname") == c.qualname:
                doc = d.get("doc") or ""
                doc_first = (doc.splitlines()[0] if isinstance(doc, str) and doc else "")
                break
        out.append({"file_path": c.path, "qualname": c.qualname, "kind": c.kind, "doc_first": doc_first})
        seen.add(k)
    return out


def select(goal: str, index: Dict, *, client: Optional[object] = None, cfg: Dict) -> List[dict]:
    """
    Return minimal list of {file_path, qualname} implementing the goal.
    Deterministic, strict JSON schema, validated against index.
    """
    options = _prefilter(
        index,
        top_k=int(cfg.get("llm_prefilter_top_k", 1200) or 1200),
        exclude_tests=cfg.get("prune_exclude_tests", True),
        noise_dirs=cfg.get("prune_noise_dirs", ["tools", "scripts", "examples", "docs"]),
    )
    keep = llm_select_spans(goal=goal, options=options, client=client, cfg=cfg)

    # Validate against index
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for it in keep:
        p = it.get("file_path")
        q = it.get("qualname")
        if not p or not q or (p, q) in seen:
            continue
        for d in (index.get(p, {}).get("defs") or []):
            if d.get("qualname") == q:
                out.append({"file_path": p, "qualname": q})
                seen.add((p, q))
                break
    return out
