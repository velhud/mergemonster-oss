from __future__ import annotations

from hashlib import sha256
from typing import Iterable


def manifest_hash(entries: Iterable[tuple[str, float, int]]) -> str:
    lines = [f"{rel}\t{mtime}\t{size}" for rel, mtime, size in entries]
    raw = "\n".join(lines).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = ["manifest_hash"]
