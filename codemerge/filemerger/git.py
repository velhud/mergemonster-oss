from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List, Sequence, Set

GIT_VENDORED_BATCH = 200


def get_git_root(root: str) -> Path | None:
    try:
        cp = subprocess.run(
            ["git", "-C", root, "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if cp.returncode != 0:
        return None
    top = (cp.stdout or "").strip()
    if not top:
        return None
    return Path(top)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except Exception:
        return False


def git_files(root: str, include_untracked: bool = False) -> List[str]:
    """Return tracked (and optionally untracked) git files under ``root``."""

    git_root = get_git_root(root)
    if git_root is None:
        return []

    scan_root = Path(root)
    try:
        scan_root = scan_root.resolve()
    except Exception:
        pass

    try:
        scan_rel = scan_root.relative_to(git_root)
    except Exception:
        return []

    def _run(args: Sequence[str]) -> List[str]:
        try:
            completed = subprocess.run(
                ["git", "-C", str(git_root), *args],
                check=False,
                capture_output=True,
                text=False,
            )
        except FileNotFoundError:
            return []
        if completed.returncode != 0:
            return []
        output = (
            completed.stdout.split(b"\x00")
            if b"\x00" in completed.stdout
            else completed.stdout.splitlines()
        )
        paths: list[str] = []
        for item in output:
            if not item:
                continue
            try:
                decoded = item.decode("utf-8", errors="replace")
            except Exception:
                continue
            repo_rel = Path(decoded)
            if scan_rel.parts and scan_rel != Path("."):
                if not _is_relative_to(repo_rel, scan_rel):
                    continue
            candidate = git_root / repo_rel
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            if resolved.is_relative_to(scan_root) and resolved.is_file():
                paths.append(str(resolved))
        return paths

    tracked = _run(["ls-files", "-z"]) or _run(["ls-files"])
    if include_untracked:
        untracked = _run(["ls-files", "--others", "--exclude-standard", "-z"]) or _run(
            ["ls-files", "--others", "--exclude-standard"]
        )
    else:
        untracked = []

    ordered: dict[str, None] = {}
    for path in tracked + untracked:
        ordered.setdefault(path, None)
    return list(ordered.keys())


def git_vendored_set(root: str, rel_paths: Iterable[str]) -> Set[str]:
    """Return paths flagged as ``linguist-vendored`` in git attributes."""

    git_root = get_git_root(root)
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

    rel_paths = [str(p) for p in rel_paths if p]
    if not rel_paths:
        return set()

    def _to_repo_rel(path: str) -> str:
        if scan_rel == Path("."):
            return Path(path).as_posix()
        return (scan_rel / Path(path)).as_posix()

    repo_rels = [_to_repo_rel(p) for p in rel_paths]
    vendored: set[str] = set()

    for start in range(0, len(repo_rels), GIT_VENDORED_BATCH):
        batch = repo_rels[start:start + GIT_VENDORED_BATCH]
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_root),
                    "check-attr",
                    "-z",
                    "linguist-vendored",
                    "--",
                    *batch,
                ],
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:
            return set()
        if completed.returncode != 0:
            continue

        stdout = completed.stdout
        parts = stdout.split(b"\x00")
        for index in range(0, len(parts) - 2, 3):
            try:
                repo_path = parts[index].decode("utf-8", errors="replace")
                value = parts[index + 2].decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if value.lower() != "set":
                continue
            repo_path_p = Path(repo_path)
            if scan_rel != Path("."):
                try:
                    rel = repo_path_p.relative_to(scan_rel)
                except Exception:
                    continue
                vendored.add(rel.as_posix())
            else:
                vendored.add(repo_path_p.as_posix())
    return vendored


__all__ = ["get_git_root", "git_files", "git_vendored_set"]
