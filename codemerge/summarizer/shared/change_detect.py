from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from codemerge.core.normalize import canonical_path

def _norm_path(p: str) -> str:
    return canonical_path(p)


def file_stat(path: str) -> Optional[Tuple[float, int]]:
    """Return (mtime, size) for an existing file; None if missing/inaccessible."""
    try:
        st = os.stat(_norm_path(path))
        return float(st.st_mtime), int(st.st_size)
    except Exception:
        return None


@dataclass(frozen=True)
class GitCtx:
    """Cache of git-changed paths for a given root and range."""

    root: str
    since_ref: str = ""  # e.g. "HEAD~1" or "main"
    until_ref: str = "HEAD"
    include_untracked: bool = False

    def changed_abs_paths(self) -> set[str]:
        root = _norm_path(self.root)
        if not Path(root, ".git").exists():
            return set()

        args: list[str] = ["git", "-C", root, "diff", "--name-only", "-z"]
        if self.since_ref:
            args.append(f"{self.since_ref}..{self.until_ref}")
        else:
            # if since_ref is empty, compare working tree vs index
            args.append(self.until_ref)

        out_paths: set[str] = set()
        try:
            cp = subprocess.run(args, check=False, capture_output=True)
            if cp.returncode == 0 and cp.stdout:
                for rel in cp.stdout.split(b"\x00"):
                    if not rel:
                        continue
                    try:
                        s = rel.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    abs_p = str(Path(root, s).resolve())
                    out_paths.add(abs_p)
        except Exception:
            pass

        if self.include_untracked:
            try:
                cp2 = subprocess.run(
                    ["git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z"],
                    check=False,
                    capture_output=True,
                )
                if cp2.returncode == 0 and cp2.stdout:
                    for rel in cp2.stdout.split(b"\x00"):
                        if not rel:
                            continue
                        try:
                            s = rel.decode("utf-8", errors="replace")
                        except Exception:
                            continue
                        abs_p = str(Path(root, s).resolve())
                        out_paths.add(abs_p)
            except Exception:
                pass

        return out_paths


def is_stale(
    path: str,
    cached_row: Optional[Dict],
    *,
    strategy: str = "mtime_size",
    git_ctx: Optional[GitCtx] = None,
    git_changed: Optional[set[str]] = None,
) -> bool:
    """
    True if a summary should be (re)generated.

    Strategies:
      - "mtime_size" (default): stale if no cache, or (mtime,size) differ.
      - "git": stale if path appears in GitCtx.changed_abs_paths(); falls back to mtime_size when git_ctx is None.
    """

    abs_path = _norm_path(path)

    # If the file is gone, treat as stale so callers can decide how to handle it.
    st = file_stat(abs_path)
    if st is None:
        return True

    if cached_row is None:
        return True

    if strategy == "git":
        changed = git_changed
        if changed is None and git_ctx is not None:
            changed = git_ctx.changed_abs_paths()
        if changed is not None and abs_path in changed:
            return True
        # fall back to mtime_size after git check

    if strategy == "sha256":
        if cached_row is None:
            return True
        cached_hash = str(cached_row.get("sha256") or "")
        try:
            current = file_sha256(abs_path)
        except Exception:
            return True
        return current != cached_hash

    cached_mtime = float(cached_row.get("mtime", 0.0))
    cached_size = int(cached_row.get("size", 0))
    if (cached_mtime, cached_size) != st:
        return True

    # If a previous attempt is marked error, allow orchestrator/backoff logic to decide.
    status = str(cached_row.get("status", "ok")).lower()
    if status != "ok":
        return True

    return False


def bulk_partition(
    paths: Iterable[str],
    cached_by_path: Dict[str, Dict],
    *,
    strategy: str = "mtime_size",
    git_ctx: Optional[GitCtx] = None,
) -> Tuple[list[str], list[str], list[str]]:
    """
    Partition into (fresh, stale, missing_in_cache) using the chosen strategy.
    Returns absolute paths.
    """

    fresh: list[str] = []
    stale: list[str] = []
    missing: list[str] = []

    cache_norm: Dict[str, Dict] = {_norm_path(k): v for k, v in cached_by_path.items()}

    git_changed: Optional[set[str]] = None
    if strategy == "git" and git_ctx is not None:
        try:
            git_changed = git_ctx.changed_abs_paths()
        except Exception:
            git_changed = None

    for p in paths:
        ap = _norm_path(p)
        row = cache_norm.get(ap)
        if row is None:
            missing.append(ap)
            stale.append(ap)
            continue
        if is_stale(ap, row, strategy=strategy, git_ctx=git_ctx, git_changed=git_changed):
            stale.append(ap)
        else:
            fresh.append(ap)

    return fresh, stale, missing


__all__ = ["GitCtx", "file_stat", "file_sha256", "is_stale", "bulk_partition"]
def file_sha256(path: str, chunk_size: int = 1 << 20) -> str:
    """Compute SHA-256 digest for ``path`` using streaming reads."""

    h = hashlib.sha256()
    with open(_norm_path(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
