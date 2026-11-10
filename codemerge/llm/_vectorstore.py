"""Vector store helpers for file_search."""

from __future__ import annotations

import json
from pathlib import Path

from ..paths import state_directory
from typing import Callable, Dict, Tuple

from ..logger import get_logger
from .clients import groq_endpoint, openai_available, get_openai_client
from .accounting import record_provider_call

logger = get_logger(__name__)



VECTOR_CACHE = state_directory() / "codemerge_vector_store.json"


def _client_base_url(client: object) -> str | None:
    for attr in ("base_url", "_base_url"):
        value = getattr(client, attr, None)
        if value:
            return str(value)
    return None


def _client_is_groq(client: object) -> bool:
    base_url = _client_base_url(client)
    return bool(base_url and base_url.startswith(groq_endpoint()))


def _ensure_vector_store(
    index: Dict[str, Dict],
    *,
    client: object | None = None,
    client_provider: Callable[[], object] | None = None,
) -> Tuple[str | None, Dict[str, str], object | None]:
    if not openai_available():
        logger.warning("OpenAI library not installed; vector store unavailable")
        return None, {}, None

    if client is None:
        if client_provider is not None:
            client = client_provider()
        else:
            client = get_openai_client()

    if client is None:
        return None, {}, None

    if _client_is_groq(client):
        logger.info("Groq endpoint detected; vector store integration skipped")
        return None, {}, client

    cache: dict = {}
    try:
        if VECTOR_CACHE.exists():
            cache = json.loads(VECTOR_CACHE.read_text())
    except Exception:
        cache = {}

    vs_id = cache.get("id")
    stored = cache.get("files", {})
    needs_rebuild = not vs_id
    for path, info in index.items():
        meta = stored.get(path)
        mtime = info.get("mtime")
        size = info.get("size")
        if not meta or meta.get("mtime") != mtime or meta.get("size") != size:
            needs_rebuild = True
            break

    try:
        if needs_rebuild:
            logger.info("rebuilding vector store")
            vs = client.beta.vector_stores.create(name="codemerge")
            vs_id = vs.id
            stored = {}
            for path, info in index.items():
                try:
                    with open(path, "rb") as f:
                        record_provider_call(provider="openai", operation="files.create")
                        up = client.files.create(file=f, purpose="assistants")
                    record_provider_call(provider="openai", operation="vector_stores.files.create")
                    client.beta.vector_stores.files.create(
                        vector_store_id=vs_id, file_id=up.id
                    )
                    stored[path] = {
                        "id": up.id,
                        "mtime": info.get("mtime"),
                        "size": info.get("size"),
                    }
                    logger.info("uploaded %s to vector store", path)
                except Exception as exc:
                    logger.warning("failed uploading %s: %s", path, exc)
                    continue
            try:
                VECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
                VECTOR_CACHE.write_text(json.dumps({"id": vs_id, "files": stored}))
            except Exception as exc:
                logger.warning("could not update vector cache: %s", exc)
        else:
            logger.info("reusing vector store")
        id_to_path = {meta["id"]: path for path, meta in stored.items()}
        return vs_id, id_to_path, client
    except Exception as exc:
        logger.error("vector store setup failed: %s", exc)
        return None, {}, client


def file_search_candidates(
    goal: str,
    index: Dict[str, Dict],
    *,
    top_k: int,
    client: object | None = None,
    client_provider: Callable[[], object] | None = None,
) -> list[tuple[str, float]]:
    vs_id, id_to_path, client = _ensure_vector_store(
        index,
        client=client,
        client_provider=client_provider,
    )
    if not vs_id or client is None:
        return []
    if _client_is_groq(client):
        logger.info("Groq endpoint detected; skipping OpenAI file_search tool")
        return []
    try:
        logger.info("file_search request start")
        record_provider_call(provider="openai", operation="responses.create", model="gpt-5-mini")
        res = client.responses.create(
            model="gpt-5-mini",
            input=[{"role": "user", "content": goal}],
            tools=[{"type": "file_search"}],
            tool_resources={"file_search": {"vector_store_ids": [vs_id]}},
            reasoning={"effort": "medium", "summary": "auto"},
            max_output_tokens=20_000,
        )
        logger.info("file_search request complete")
    except Exception as exc:
        logger.error("file_search request failed: %s", exc)
        return []

    cands: list[tuple[str, float]] = []
    try:
        outputs = getattr(res, "output", []) or []
        for item in outputs:
            content_list = getattr(item, "content", None)
            if content_list is None and isinstance(item, dict):
                content_list = item.get("content")
            if not content_list:
                continue
            for content in content_list:
                fs = content.get("file_search") if isinstance(content, dict) else None
                if not fs:
                    continue
                for r in fs.get("results", []):
                    fid = r.get("file_id")
                    path = id_to_path.get(fid)
                    if not path:
                        continue
                    try:
                        score = float(r.get("score", 0.0) or 0.0)
                    except Exception:
                        score = 0.0
                    cands.append((path, score))
    except Exception as exc:
        logger.error("parsing file_search results failed: %s", exc)
        return []

    cands.sort(key=lambda c: c[1], reverse=True)
    logger.info("file_search returned %d candidates", len(cands))
    return cands[:top_k]
