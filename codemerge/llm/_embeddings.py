"""Embeddings utilities and caching."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..paths import state_directory
from typing import List, Sequence

from .embed_types import (
    EmbedProvider,
    DEFAULT_GROQ_EMBED_MODEL,
    DEFAULT_OPENAI_EMBED_MODEL,
    max_dimensions_for_model,
    supports_custom_dimensions,
)
from ..logger import get_logger
from .clients import openai_available, get_openai_client
from .accounting import record_provider_call

logger = get_logger(__name__)

EMBED_HITS = 0
EMBED_MISSES = 0



def _default_embed_db(model_name: str) -> Path:
    suffix = model_name.replace("-", "_") if model_name else "none"
    return state_directory() / f"codemerge_embed_{suffix}.db"


EMBED_DB: Path | str | dict | None = _default_embed_db(DEFAULT_GROQ_EMBED_MODEL)


def _embed_db_path(model_name: str) -> Path:
    override = EMBED_DB
    if isinstance(override, dict):
        key = model_name or DEFAULT_GROQ_EMBED_MODEL
        candidate = override.get(key) or override.get(DEFAULT_GROQ_EMBED_MODEL)
        if candidate:
            return Path(candidate).expanduser()
    elif override:
        return Path(override).expanduser()
    return _default_embed_db(model_name)


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeds (
            file TEXT,
            qualname TEXT,
            mtime REAL,
            size INTEGER,
            model TEXT,
            dimensions INTEGER,
            vec TEXT,
            PRIMARY KEY (file, qualname, mtime, size, model, dimensions)
        )
        """
    )


def _cached_embedding(
    conn: sqlite3.Connection,
    file: str,
    qualname: str,
    mtime: float,
    size: int,
    model: str,
    dimensions: int,
) -> List[float] | None:
    row = conn.execute(
        "SELECT vec FROM embeds WHERE file=? AND qualname=? AND mtime=? AND size=? AND model=? AND dimensions=?",
        (file, qualname, mtime, size, model, dimensions),
    ).fetchone()
    if not row:
        return None
    try:
        return list(json.loads(row[0]))
    except Exception:
        return None


def _store_embedding(
    conn: sqlite3.Connection,
    file: str,
    qualname: str,
    mtime: float,
    size: int,
    model: str,
    dimensions: int,
    vec: Sequence[float],
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO embeds VALUES (?,?,?,?,?,?,?)",
        (file, qualname, mtime, size, model, dimensions, json.dumps(list(vec))),
    )
    conn.commit()


def _dim_key(dimensions: int | None) -> int:
    if dimensions is None:
        return -1
    try:
        val = int(dimensions)
    except Exception:
        return -1
    return val if val >= 0 else -1


def resolve_embedding_provider(provider: EmbedProvider) -> EmbedProvider:
    if provider is EmbedProvider.GROQ:
        logger.info("Groq embeddings unavailable. Select OpenAI explicitly to enable embeddings.")
        return EmbedProvider.NONE
    return provider


def get_embedding(
    text: str,
    provider: EmbedProvider,
    cache_key: tuple[str, str, float, int] | None,
    *,
    model: str | None = None,
    dimensions: int | None = None,
    encoding_format: str = "float",
) -> List[float] | None:

    if provider is EmbedProvider.NONE:
        logger.info("embedding provider NONE; skipping")
        return None

    if provider is EmbedProvider.GROQ:
        provider = resolve_embedding_provider(provider)
        if provider is EmbedProvider.NONE:
            return None

    if provider is EmbedProvider.OPENAI:
        model_name = (model or DEFAULT_OPENAI_EMBED_MODEL).strip()
        if not model_name:
            logger.error("OpenAI embedding model is undefined")
            return None
        dim_override = None
        if dimensions is not None and dimensions > 0:
            if not supports_custom_dimensions(model_name):
                logger.info(
                    "Model %s does not support custom dimensions; ignoring override %s",
                    model_name,
                    dimensions,
                )
            else:
                limit = max_dimensions_for_model(model_name)
                if limit is not None and dimensions > limit:
                    logger.info(
                        "Clamping embedding dimensions %s->%s for model %s",
                        dimensions,
                        limit,
                        model_name,
                    )
                    dim_override = limit
                else:
                    dim_override = int(dimensions)

        file = qualname = ""
        mtime = 0.0
        size = 0
        conn: sqlite3.Connection | None = None
        cache_enabled = bool(cache_key)
        if cache_enabled:
            assert cache_key is not None
            file, qualname, mtime, size = cache_key
            db_path = _embed_db_path(model_name)
            try:
                try:
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                conn = sqlite3.connect(str(db_path), timeout=30)
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except Exception:
                    pass
                _ensure_db(conn)
                vec = _cached_embedding(
                    conn,
                    file,
                    qualname,
                    mtime,
                    size,
                    model_name,
                    _dim_key(dim_override),
                )
                if vec is not None:
                    logger.debug("embedding cache hit for %s", file)
                    global EMBED_HITS
                    EMBED_HITS += 1
                    return vec
            except Exception as exc:
                logger.warning("embedding cache unavailable: %s", exc)
                conn = None
        try:
            if not openai_available():
                logger.warning("OpenAI library not installed; cannot compute embeddings")
                return None
            client = get_openai_client()
            descriptor = file if cache_enabled else "<text>"
            logger.debug("requesting embedding for %s", descriptor)
            payload: dict[str, object] = {
                "model": model_name,
                "input": text.replace("\n", " "),
            }
            if dim_override is not None:
                payload["dimensions"] = dim_override
            enc = encoding_format.strip().lower()
            if enc not in {"", "float", "base64"}:
                logger.warning(
                    "Unsupported encoding_format %s; defaulting to float",
                    encoding_format,
                )
                enc = "float"
            if enc == "base64":
                payload["encoding_format"] = enc
            record_provider_call(provider="openai", operation="embeddings.create", model=model_name)
            res = client.embeddings.create(**payload)
            vec = res.data[0].embedding
            logger.debug("embedding ok for %s", descriptor)
            global EMBED_MISSES
            EMBED_MISSES += 1
            if conn is not None and cache_enabled:
                try:
                    _store_embedding(
                        conn,
                        file,
                        qualname,
                        mtime,
                        size,
                        model_name,
                        _dim_key(dim_override),
                        vec,
                    )
                except Exception:
                    pass
            return vec
        except Exception as exc:
            logger.error("embedding request failed: %s", exc)
            return None
        finally:
            if conn is not None:
                conn.close()

    return None
