from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Set

from .. import FileProcessor, git as git_mod

log = logging.getLogger(__name__)


def changed_set(root: str, since_ref: str, until_ref: str) -> Set[str]:
    """Return set of relative paths changed between ``since_ref`` and ``until_ref``."""

    root = root or "."
    since_ref = (since_ref or "").strip()
    until_ref = (until_ref or "HEAD").strip() or "HEAD"

    if not since_ref:
        return set()

    git_root = git_mod.get_git_root(root)
    if git_root is None:
        return set()

    scan_root = Path(root)
    try:
        scan_root = scan_root.resolve()
    except Exception:
        pass
    try:
        scan_rel = scan_root.relative_to(git_root)
    except Exception:
        return set()

    try:
        cp = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "--verify", since_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if cp.returncode != 0:
            log.debug("rev-parse failed for %s: %s", since_ref, cp.stderr.strip())
            return set()

        diff_cmd = [
            "git",
            "-C",
            str(git_root),
            "diff",
            "--name-only",
            f"{since_ref}..{until_ref}",
        ]
        cp = subprocess.run(diff_cmd, capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            log.debug("git diff failed: %s", cp.stderr.strip())
            return set()
        rels: Set[str] = set()
        for line in cp.stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            repo_rel = Path(raw)
            if scan_rel != Path("."):
                try:
                    rel = repo_rel.relative_to(scan_rel)
                except Exception:
                    continue
                rels.add(rel.as_posix())
            else:
                rels.add(repo_rel.as_posix())
        return rels
    except Exception:
        log.exception("Failed to compute git changed set")
        return set()

def vendored_set(fp: FileProcessor, root: str, rel_paths: List[str]) -> Set[str]:
    """Wrap FileProcessor.git_vendored_set()."""
    try:
        return set(fp.git_vendored_set(root, rel_paths))
    except Exception:
        return set()
