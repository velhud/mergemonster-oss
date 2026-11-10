

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence
import json
import hashlib
import math
import os
import re
from ..llm.embed_types import (
    EmbedProvider,
    DEFAULT_GROQ_EMBED_MODEL,
    DEFAULT_OPENAI_EMBED_MODEL,
)
from ..logger import get_logger, log_event
import fnmatch
from ..llm import retrieval as llm_retrieval

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    path: str
    kind: str  # "def" | "class" | "file"
    qualname: str | None
    start: int | None
    end: int | None
    score: float
    reason: str


# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------

STOPWORDS = {"the", "a", "an", "and", "or", "to", "for", "of", "on"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t and t not in STOPWORDS}


def _overlap(goal: Sequence[str], tokens: Iterable[str], weight: float) -> float:
    token_set = set(tokens)
    return sum(weight for g in goal if g in token_set)


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0]


# ---------------------------------------------------------------------------
# Embeddings utilities
# ---------------------------------------------------------------------------

# LLM embedding/rerank calls live in codemerge.llm.retrieval.

def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# File search utilities
# ---------------------------------------------------------------------------

# LLM file_search calls live in codemerge.llm.retrieval.

# Ranking
# ---------------------------------------------------------------------------


def rank_candidates(
    goal: str,
    index: Dict[str, Dict],
    top_k: int = 200,
    use_embeddings: bool = False,
    embedding_provider: str = "groq",
    retrieval_backend: str = "embeddings",
    ranking_mode: str = "hybrid",
    embedding_model: str = DEFAULT_GROQ_EMBED_MODEL,
    openai_embedding_model: str | None = None,
    embedding_dimensions: int | None = None,
    openai_embedding_dimensions: int | None = None,
    embedding_encoding_format: str = "float",
    *,
    seed_files: list[str] | None = None,
    seed_defs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    exclude_exts: list[str] | None = None,
    allowed_paths: set[str] | None = None,
    exclude_tests: bool = True,
    downrank_noise: bool = True,
    noise_dirs: list[str] | None = None,
) -> List[Candidate]:

    llm_retrieval.EMBED_HITS = llm_retrieval.EMBED_MISSES = 0
    seed_files = seed_files or []
    seed_defs = seed_defs or []
    exclude_globs = exclude_globs or []
    exclude_exts = {e.lstrip(".").lower() for e in (exclude_exts or [])}
    if allowed_paths is not None:
        allowed_paths = {str(Path(p).resolve()) for p in allowed_paths}
    noise_dirs = [d.strip("/").lower() for d in (noise_dirs or ["tools", "scripts", "examples", "docs"])]
    # counters for logging
    exc_ext = 0
    exc_glob = 0
    exc_tests = 0
    # Token and embedding setup (restored after adding seed/exclude params)
    goal_tokens = list(_tokenize(goal))
    log_event(logger, "retrieval_goal", goal_token_count=len(goal_tokens))
    base_model = (embedding_model or DEFAULT_GROQ_EMBED_MODEL).strip()
    base_dims = (
        int(embedding_dimensions)
        if isinstance(embedding_dimensions, int) and embedding_dimensions > 0
        else None
    )
    openai_model = (openai_embedding_model or DEFAULT_OPENAI_EMBED_MODEL or "").strip()
    if not openai_model:
        openai_model = DEFAULT_OPENAI_EMBED_MODEL
    openai_dims = (
        int(openai_embedding_dimensions)
        if isinstance(openai_embedding_dimensions, int)
        and openai_embedding_dimensions > 0
        else None
    )
    encoding_fmt = (embedding_encoding_format or "float").strip().lower() or "float"

    provider_enum = (
        EmbedProvider(embedding_provider)
        if use_embeddings and retrieval_backend == "embeddings"
        else EmbedProvider.NONE
    )
    provider = llm_retrieval.resolve_embedding_provider(provider_enum)
    provider_value = provider.value
    mode = (ranking_mode or "hybrid").lower()
    do_lexical = mode in {"keywords", "hybrid", "hybrid_rerank"}
    do_embed = mode in {"embeddings", "hybrid", "hybrid_rerank"} and provider is not EmbedProvider.NONE
    goal_embed: List[float] | None = None

    def _effective_embed_settings(current: EmbedProvider) -> tuple[str | None, int | None]:
        if current is EmbedProvider.OPENAI:
            return (openai_model or DEFAULT_OPENAI_EMBED_MODEL, openai_dims)
        return (base_model, base_dims)

    embed_model_name, embed_dim_override = _effective_embed_settings(provider)

    log_event(
        logger,
        "rank_start",
        backend=retrieval_backend,
        mode=ranking_mode,
        embeddings=use_embeddings,
        provider=provider_value,
        embed_model=embed_model_name,
        embed_dims=embed_dim_override or 0,
        seed_files=len(seed_files),
        seed_defs=len(seed_defs),
        allowed_paths=bool(allowed_paths),
    )

    if do_embed:
        digest = hashlib.sha1(goal.encode("utf-8")).hexdigest() if goal else ""
        goal_cache_key = (f"<goal>:{digest}", "", 0.0, len(goal))
        goal_embed = llm_retrieval.get_embedding(
            goal,
            provider,
            goal_cache_key,
            model=embed_model_name,
            dimensions=embed_dim_override,
            encoding_format=encoding_fmt,
        )
        if goal_embed is None:
            logger.warning("Embeddings unavailable; falling back to lexical-only")
            provider = EmbedProvider.NONE
            do_embed = False

    # Skip paths matching any exclude patterns
    def _skip_path(path: str) -> bool:
        p = Path(path)
        abs_path = str(p.resolve())
        if allowed_paths is not None and abs_path not in allowed_paths:
            return True
        # Ext exclude
        if p.suffix.lower().lstrip(".") in exclude_exts:
            nonlocal exc_ext
            exc_ext += 1
            return True
        sp = p.as_posix()
        # Tests auto-exclude only when not in strict UI scope
        if exclude_tests:
            name = p.name.lower()
            if "tests" in sp.split("/") or name.startswith("test_") or sp.endswith("_test.py"):
                nonlocal exc_tests
                exc_tests += 1
                return True
        # Glob excludes
        for g in exclude_globs:
            try:
                if fnmatch.fnmatch(sp, g):
                    nonlocal exc_glob
                    exc_glob += 1
                    return True
            except Exception:
                continue
        return False

    cands: List[Candidate] = []
    seen_keys: set[tuple[str, str | None]] = set()

    # Hard include seed files (as file candidates) first
    for spath in seed_files:
        if spath and not _skip_path(spath):
            c = Candidate(spath, "file", None, None, None, 1e6, "seed")
            cands.append(c)
            seen_keys.add((spath, None))
    for path, info in index.items():
        if _skip_path(path):
            continue
        # Allowed paths filter takes precedence; if path is outside, skip early
        if allowed_paths is not None and str(Path(path).resolve()) not in allowed_paths:
            continue
        path_tokens = _tokenize(Path(path).stem)
        dir_tokens = _tokenize("/".join(Path(path).parts[:-1]))
        noise_penalty = 0.0
        if downrank_noise and (path not in seed_files):
            sp = Path(path).as_posix().lower()
            if any(f"/{d}/" in f"/{sp}/" for d in noise_dirs):
                noise_penalty = -0.35  # small, consistent penalty
        if info.get("defs"):
            for d in info.get("defs"):
                qual_tokens = _tokenize(d.get("qualname", ""))
                doc_first = _first_line(d.get("doc"))
                doc_tokens = _tokenize(doc_first)
                score = 0.0
                if do_lexical:
                    score += _overlap(goal_tokens, qual_tokens, 2.0)
                    score += _overlap(goal_tokens, path_tokens, 1.5)
                    score += _overlap(goal_tokens, doc_tokens, 0.75)
                    score += _overlap(goal_tokens, dir_tokens, 0.25)
                if score:
                    reason = "lexical"
                    if do_embed and goal_embed is not None:
                        key = (
                            path,
                            d.get("qualname", ""),
                            info.get("mtime", 0.0),
                            info.get("size", 0),
                        )
                        vec = llm_retrieval.get_embedding(
                            d.get("qualname", "") + " " + doc_first,
                            provider,
                            key,
                            model=embed_model_name,
                            dimensions=embed_dim_override,
                            encoding_format=encoding_fmt,
                        )
                        if vec is not None:
                            cos = _cosine(goal_embed, vec)
                            if mode in {"hybrid", "hybrid_rerank"}:
                                score = 0.4 * score + 0.6 * cos
                                reason = "hybrid"
                            elif mode == "embeddings":
                                score = cos
                                reason = "embed_only"
                        elif do_embed:
                            continue
                elif do_embed and goal_embed is not None:
                    vec = llm_retrieval.get_embedding(
                        (d.get("qualname", "") + " " + doc_first).strip(),
                        provider,
                        (path, d.get("qualname", ""), info.get("mtime", 0.0), info.get("size", 0)),
                        model=embed_model_name,
                        dimensions=embed_dim_override,
                        encoding_format=encoding_fmt,
                    )
                    if vec is None:
                        continue
                    cos = _cosine(goal_embed, vec)
                    score = cos
                    reason = "embed_only"
                if score:
                    cand = Candidate(
                        path,
                        d.get("kind", "def"),
                        d.get("qualname"),
                        d.get("start"),
                        d.get("end"),
                        score + noise_penalty,
                        reason,
                    )
                    cands.append(cand)
                    seen_keys.add((cand.path, cand.qualname))
                    # Force-include seed defs (give them an extra boost) — but do not duplicate append
                    if d.get("qualname") in seed_defs:
                        cand.score += 1e5
                        cand.reason = "seed"
        else:
            score = 0.0
            if do_lexical:
                score += _overlap(goal_tokens, path_tokens, 1.5)
                score += _overlap(goal_tokens, dir_tokens, 0.25)
            if score:
                key = (path, None)
                if key in seen_keys:
                    continue
                cand = Candidate(path, "file", None, None, None, score + noise_penalty, "lexical")
                cands.append(cand)
                seen_keys.add((cand.path, cand.qualname))
            elif do_embed and goal_embed is not None:
                # Embeddings-only fallback for files without defs
                # Use a simple file-level embedding keyed by the path text
                mtime = info.get("mtime", 0.0)
                size = info.get("size", 0)
                file_text = Path(path).as_posix()
                vec = llm_retrieval.get_embedding(
                    file_text,
                    provider,
                    (path, "<FILE>", mtime, size),
                    model=embed_model_name,
                    dimensions=embed_dim_override,
                    encoding_format=encoding_fmt,
                )
                if vec is not None:
                    cos = _cosine(goal_embed, vec)
                    if cos:
                        key = (path, None)
                        if key in seen_keys:
                            continue
                        cand = Candidate(path, "file", None, None, None, cos + noise_penalty, "embed_only_file")
                        cands.append(cand)
                        seen_keys.add((cand.path, cand.qualname))

    cands.sort(key=lambda c: c.score, reverse=True)

    # Optional LLM rerank pass for file-level items
    if mode == "hybrid_rerank" and cands:
        file_items = [(c.path, c.score) for c in cands if c.kind == "file"]
        if file_items:
            try:
                ordered = llm_retrieval.rerank_with_llm(goal, file_items, client=None, model_name="gpt-5-mini")
                order_map = {p: i for i, p in enumerate(ordered)}

                def _order_key(c: Candidate) -> tuple:
                    # File and def candidates both prefer the reranked file order
                    base = order_map.get(c.path, 10_000_000)
                    return (base, -c.score)

                cands.sort(key=_order_key)
            except Exception as e:
                logger.warning("LLM rerank failed; using hybrid order: %s", e)

    # Optional semantic recall fill: if embeddings enabled and we still have room,
    # probe a capped number of unseen defs by cosine and add the best matches.
    if (
        provider is not EmbedProvider.NONE
        and goal_embed is not None
        and len(cands) < top_k
    ):
        deficit = top_k - len(cands)
        max_probe = min(800, max(0, deficit * 8))  # small, capped probe
        scored: list[tuple[float, Candidate]] = []
        probed = 0
        for path, info in index.items():
            defs = info.get("defs") or []
            mtime = info.get("mtime", 0.0)
            size = info.get("size", 0)
            for d in defs:
                key2 = (path, d.get("qualname"))
                if key2 in seen_keys:
                    continue
                qual = d.get("qualname", "")
                doc_first = _first_line(d.get("doc"))
                txt = (qual + " " + doc_first).strip()
                if not txt:
                    continue
                vec = llm_retrieval.get_embedding(
                    txt,
                    provider,
                    (path, qual, mtime, size),
                    model=embed_model_name,
                    dimensions=embed_dim_override,
                    encoding_format=encoding_fmt,
                )
                if vec is None:
                    continue
                cos = _cosine(goal_embed, vec)
                if cos <= 0:
                    continue
                scored.append(
                    (
                        cos,
                        Candidate(
                            path,
                            d.get("kind", "def"),
                            qual,
                            d.get("start"),
                            d.get("end"),
                            cos,
                            "embed_only",
                        ),
                    )
                )
                probed += 1
                if probed >= max_probe:
                    break
            if probed >= max_probe:
                break
        if scored:
            scored.sort(key=lambda t: t[0], reverse=True)
            for _score, cand in scored[:deficit]:
                cands.append(cand)
                seen_keys.add((cand.path, cand.qualname))
            cands.sort(key=lambda c: c.score, reverse=True)

    log_event(
        logger,
        "retrieval_filters",
        allowed_paths=bool(allowed_paths),
        exclude_tests=exclude_tests,
        exc_ext=exc_ext,
        exc_glob=exc_glob,
        exc_tests=exc_tests,
    )
    top_sample = [
        {
            "path": c.path,
            "kind": c.kind,
            "score": round(c.score, 4),
            "reason": c.reason,
        }
        for c in cands[:10]
    ]
    log_event(
        logger,
        "rank_end",
        produced=len(cands),
        top=top_sample,
    )
    if use_embeddings and llm_retrieval.EMBED_HITS + llm_retrieval.EMBED_MISSES:
        log_event(
            logger,
            "embedding_batch",
            hits=llm_retrieval.EMBED_HITS,
            misses=llm_retrieval.EMBED_MISSES,
            provider=provider_value,
        )
    if (exc_ext + exc_glob + exc_tests) > 0:
        logger.info(
            json.dumps(
                {
                    "event": "retrieval_filters",
                    "exc_ext": exc_ext,
                    "exc_glob": exc_glob,
                    "exc_tests": exc_tests,
                    "allowed_paths": bool(allowed_paths),
                    "exclude_tests": exclude_tests,
                }
            )
        )
    return cands[:top_k]


# ---------------------------------------------------------------------------
# Neighbour expansion
# ---------------------------------------------------------------------------


def expand_neighbors(
    cands: List[Candidate],
    index: Dict[str, Dict],
    max_neighbors: int = 100,
    *,
    seed_paths: set[str] | None = None,
    neighbors_mode: str = "same_package",
    allowed_paths: set[str] | None = None,
) -> List[Candidate]:
    if neighbors_mode == "none":
        return cands

    log_event(
        logger,
        "neighbors_start",
        mode=neighbors_mode,
        max=max_neighbors,
        allowed_paths=bool(allowed_paths),
        seed_roots_count=len(seed_paths or []),
    )
    added: List[Candidate] = []
    seen = {(c.path, c.qualname) for c in cands}
    total = 0
    reasons = {"import": 0, "attr": 0, "same_file": 0}
    unresolved_imports = 0
    skipped_out = 0
    root = Path(os.path.commonpath(index.keys())) if index else Path(".")
    # Limit expansion to same top-level dirs as seeds (if provided)
    seed_roots: set[str] = set()
    if seed_paths:
        for sp in seed_paths:
            try:
                rel = Path(sp).relative_to(root)
            except Exception:
                rel = Path(sp)
            parts = rel.parts
            if parts:
                seed_roots.add(parts[0])
    def _within_seed_roots(path: str) -> bool:
        if not seed_roots:
            return True
        try:
            rel = Path(path).relative_to(root)
            return (rel.parts[0] if rel.parts else "") in seed_roots
        except Exception:
            return True
    def _allowed(path: str) -> bool:
        if allowed_paths is None:
            return True
        try:
            return str(Path(path).resolve()) in allowed_paths
        except Exception:
            return False

    for cand in list(cands):
        if total >= max_neighbors:
            break
        info = index.get(cand.path)
        if not info:
            continue

        # Same file helpers referenced by name or attribute
        if cand.kind in {"def", "class"} and cand.qualname:
            if neighbors_mode == "same_file":
                # Only same-file refs; skip import-based expansion
                pass
            raw_refs = info.get("refs", {}).get(cand.qualname, [])
            name_refs = {r for r in raw_refs if isinstance(r, str)}
            attr_refs = [tuple(r) for r in raw_refs if isinstance(r, (tuple, list))]

            if name_refs:
                for d in info.get("defs", []):
                    key = (cand.path, d.get("qualname"))
                    if key in seen or d.get("qualname") == cand.qualname:
                        continue
                    name = d.get("name")
                    if name and name in name_refs:
                        added.append(
                            Candidate(
                                cand.path,
                                d.get("kind", "def"),
                                d.get("qualname"),
                                d.get("start"),
                                d.get("end"),
                                cand.score,
                                "neighbor",
                            )
                        )
                        seen.add(key)
                        total += 1
                        reasons["same_file"] += 1
                        if total >= max_neighbors:
                            break

            if total >= max_neighbors:
                break

            # Attribute references to imported modules
            if neighbors_mode != "same_file":
                iterable_attrs = attr_refs
            else:
                iterable_attrs = []
            for mod, attr in iterable_attrs:
                target_path: Path | None = None

                for imp in info.get("imports", []):
                    if imp.get("module") != mod:
                        continue
                    level = int(imp.get("level", 0))
                    base = Path(cand.path).parent
                    for _ in range(max(level - 1, 0)):
                        base = base.parent
                    parts = [p for p in mod.split(".") if p]
                    mod_path = base
                    for part in parts:
                        mod_path = mod_path / part
                    for candidate_path in [
                        mod_path.with_suffix(".py"),
                        mod_path / "__init__.py",
                    ]:
                        if candidate_path.exists():
                            target_path = candidate_path
                            break
                    if target_path:
                        break

                if not target_path:
                    mod_full = mod
                    for path in index.keys():
                        try:
                            rel = Path(path).relative_to(root)
                        except Exception:
                            continue
                        rel_mod = ".".join(rel.with_suffix("").parts)
                        if rel_mod == mod_full and Path(path).exists():
                            target_path = Path(path)
                            break

                if not target_path:
                    unresolved_imports += 1
                    continue

                if not _allowed(str(target_path)):
                    skipped_out += 1
                    continue
                mod_info = index.get(str(target_path))
                qual = None
                kind = "file"
                start = end = None
                if mod_info:
                    for d in mod_info.get("defs", []):
                        if d.get("name") == attr:
                            qual = d.get("qualname")
                            kind = d.get("kind", "def")
                            start = d.get("start")
                            end = d.get("end")
                            break

                key = (str(target_path), qual)
                if key in seen:
                    continue

                added.append(
                    Candidate(
                        str(target_path),
                        kind,
                        qual,
                        start,
                        end,
                        cand.score,
                        "attr",
                    )
                )
                seen.add(key)
                total += 1
                reasons["attr"] += 1
                if total >= max_neighbors:
                    break

        if total >= max_neighbors:
            break

        # Resolve relative and absolute imports with levels
        if neighbors_mode != "same_file":
            imports_iter = info.get("imports", [])
        else:
            imports_iter = []
        for imp in imports_iter:
            module = imp.get("module", "")
            level = int(imp.get("level", 0))
            base = Path(cand.path).parent
            for _ in range(max(level - 1, 0)):
                base = base.parent
            parts = [p for p in module.split(".") if p]
            mod_path = base
            for part in parts:
                mod_path = mod_path / part

            candidates = [mod_path.with_suffix(".py"), mod_path / "__init__.py"]
            resolved = False
            for candidate_path in candidates:
                if candidate_path.exists():
                    if not _within_seed_roots(str(candidate_path)):
                        continue
                    if not _allowed(str(candidate_path)):
                        continue
                    key = (str(candidate_path), None)
                    if key not in seen:
                        added.append(
                            Candidate(
                                str(candidate_path),
                                "file",
                                None,
                                None,
                                None,
                                cand.score,
                                "import",
                            )
                        )
                        seen.add(key)
                        total += 1
                        reasons["import"] += 1
                    resolved = True
                    break

            if not resolved:
                mod_full = ".".join(parts)
                for path in index.keys():
                    try:
                        rel = Path(path).relative_to(root)
                    except Exception:
                        continue
                    rel_mod = ".".join(rel.with_suffix("").parts)
                    if rel_mod == mod_full and Path(path).exists():
                        if not _allowed(path):
                            continue
                        key = (path, None)
                        if key not in seen and _within_seed_roots(path):
                            added.append(
                                Candidate(path, "file", None, None, None, cand.score, "import")
                            )
                            seen.add(key)
                            total += 1
                            reasons["import"] += 1
                        resolved = True
                        break

            if total >= max_neighbors:
                break

    result = cands + added
    log_event(
        logger,
        "neighbors_end",
        added=len(added),
        reasons=reasons,
        unresolved_imports=unresolved_imports,
        skipped_out_of_scope=skipped_out,
    )
    return result
