"""Tokenization helpers shared across MergeMonster components."""

from __future__ import annotations

import base64
import functools
import gzip
import hashlib
from importlib.resources import files
from typing import Iterable

import tiktoken

ENCODING_NAME = "o200k_base"
ENCODING_TAG_SUFFIX = "|normnl"
ENCODING_TAG = f"{ENCODING_NAME}{ENCODING_TAG_SUFFIX}"
_RANKS_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
# Pattern and special tokens adapted from OpenAI tiktoken (MIT).
_PATTERN = "|".join((
    r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
    r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
    r"""\p{N}{1,3}""",
    r""" ?[^\s\p{L}\p{N}]+[\r\n/]*""",
    r"""\s*[\r\n]+""",
    r"""\s+(?!\S)""",
    r"""\s+""",
))


@functools.lru_cache(maxsize=1)
def get_encoding() -> tiktoken.Encoding:
    """Load the exact bundled ranks with no network or external cache."""
    ranks_file = files(__package__).joinpath("data", "o200k_base.tiktoken.gz")
    payload = gzip.decompress(ranks_file.read_bytes())
    if hashlib.sha256(payload).hexdigest() != _RANKS_SHA256:
        raise RuntimeError("The bundled o200k_base tokenizer data failed verification.")
    ranks = {}
    for line in payload.splitlines():
        token, rank = line.split()
        ranks[base64.b64decode(token)] = int(rank)
    return tiktoken.Encoding(
        name=ENCODING_NAME,
        pat_str=_PATTERN,
        mergeable_ranks=ranks,
        special_tokens={"<|endoftext|>": 199999, "<|endofprompt|>": 200018},
    )


def get_encoding_tag() -> str:
    """Return the cache tag used to invalidate stale token cache rows."""
    return ENCODING_TAG


def count_tokens(text: str, *, encoding: tiktoken.Encoding | None = None) -> int:
    """Return the number of tokens for ``text`` using the shared encoder."""
    enc = encoding or get_encoding()
    return len(enc.encode(text, disallowed_special=()))


def count_tokens_iter(chunks: Iterable[str], *, encoding: tiktoken.Encoding | None = None) -> int:
    """Return token count for a sequence of strings without reloading encoder."""
    total = 0
    enc = encoding or get_encoding()
    for chunk in chunks:
        total += len(enc.encode(chunk, disallowed_special=()))
    return total


__all__ = [
    "ENCODING_NAME",
    "ENCODING_TAG_SUFFIX",
    "ENCODING_TAG",
    "count_tokens",
    "count_tokens_iter",
    "get_encoding",
    "get_encoding_tag",
    "assert_o200k_available",
]


def assert_o200k_available() -> None:
    """Fail fast when the required encoder is unavailable."""

    get_encoding()
