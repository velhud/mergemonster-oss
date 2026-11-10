"""LLM-backed retrieval helpers (embeddings, rerank, file search)."""

from __future__ import annotations

import sys
import types

from . import _embeddings as _embeddings
from . import _vectorstore as _vectorstore
from ._rerank import rerank_with_llm

EMBED_HITS = _embeddings.EMBED_HITS
EMBED_MISSES = _embeddings.EMBED_MISSES
EMBED_DB = _embeddings.EMBED_DB
VECTOR_CACHE = _vectorstore.VECTOR_CACHE


def _sync_counters() -> None:
    globals()["EMBED_HITS"] = _embeddings.EMBED_HITS
    globals()["EMBED_MISSES"] = _embeddings.EMBED_MISSES
    globals()["EMBED_DB"] = _embeddings.EMBED_DB


def resolve_embedding_provider(provider):
    return _embeddings.resolve_embedding_provider(provider)


def get_embedding(*args, **kwargs):
    result = _embeddings.get_embedding(*args, **kwargs)
    _sync_counters()
    return result


def file_search_candidates(*args, **kwargs):
    return _vectorstore.file_search_candidates(*args, **kwargs)


class _Module(types.ModuleType):
    def __setattr__(self, name, value):
        if name in {"EMBED_HITS", "EMBED_MISSES", "EMBED_DB"}:
            setattr(_embeddings, name, value)
        return super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Module

__all__ = [
    "EMBED_HITS",
    "EMBED_MISSES",
    "EMBED_DB",
    "VECTOR_CACHE",
    "resolve_embedding_provider",
    "get_embedding",
    "rerank_with_llm",
    "file_search_candidates",
]
