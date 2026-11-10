from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from hashlib import sha256
from typing import Iterable, Sequence

from codemerge.llm.reducer import format_item


ANCHOR_FILE_PATTERNS = [
    "README*",
    "readme*",
    "package.json",
    "pyproject.toml",
    "requirements*.txt",
    "Makefile",
    "Dockerfile",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    ".env.example",
]


@dataclass(frozen=True)
class ChildStat:
    path: str
    kind: str  # "file" | "folder"
    size_bytes: int | None = None


def is_anchor_filename(name: str) -> bool:
    base = str(name or "").strip()
    if not base:
        return False
    for pattern in ANCHOR_FILE_PATTERNS:
        if fnmatch(base, pattern):
            return True
    return False


def build_inventory_list(items: Iterable[str]) -> str:
    entries = [str(x).strip() for x in items if str(x).strip()]
    if not entries:
        return "(empty)"
    lines = [f"- {item}" for item in sorted(entries)]
    return "\n".join(lines)


def build_child_stats(items: Iterable[ChildStat]) -> str:
    rows = []
    for item in items:
        size = "" if item.size_bytes is None else str(int(item.size_bytes))
        rows.append(f"{item.path} | {item.kind} | {size}".strip())
    if not rows:
        return "(none)"
    return "\n".join(rows)


def build_tree_snapshot(paths: Iterable[str], *, max_depth: int = 2, max_entries: int = 300) -> str:
    normalized = []
    for path in paths:
        rel = str(path or "").strip().strip("/")
        if rel:
            normalized.append(rel)
    if not normalized:
        return "(empty)"

    folder_nodes: set[tuple[str, ...]] = set()
    tree: dict[str, dict] = {}
    for rel in normalized:
        parts = [p for p in rel.split("/") if p]
        for idx in range(1, len(parts)):
            folder_nodes.add(tuple(parts[:idx]))
        node = tree
        for part in parts[:max_depth]:
            node = node.setdefault(part, {})
        if len(parts) > max_depth:
            folder_nodes.add(tuple(parts[:max_depth]))

    lines: list[str] = []

    def _walk(node: dict, prefix: list[str], depth: int) -> None:
        nonlocal lines
        if len(lines) >= max_entries:
            return
        for name in sorted(node.keys()):
            if len(lines) >= max_entries:
                return
            path_key = tuple(prefix + [name])
            label = f"{name}/" if path_key in folder_nodes else name
            indent = "  " * depth
            lines.append(f"{indent}- {label}")
            child = node.get(name, {})
            if child and depth + 1 < max_depth:
                _walk(child, prefix + [name], depth + 1)

    _walk(tree, [], 0)
    if len(lines) >= max_entries:
        lines.append("- ...")
    return "\n".join(lines) if lines else "(empty)"


def format_evidence_blocks(items: Sequence[tuple[str, str]]) -> str:
    if not items:
        return "(none)"
    parts = [format_item(label, text) for label, text in items]
    return "\n\n".join(parts).strip()


def format_anchor_blocks(items: Sequence[tuple[str, str]]) -> str:
    if not items:
        return "(none)"

    def _fence(text: str) -> str:
        safe = str(text or "").replace("```", "``\\`")
        return f"```text\n{safe}\n```"

    parts = [format_item(label, _fence(text)) for label, text in items]
    return "\n\n".join(parts).strip()


def compute_inputs_hash(items: Sequence[tuple[str, str]]) -> str:
    lines = []
    for path, summary_text in sorted(items, key=lambda x: x[0]):
        digest = sha256(str(summary_text or "").encode("utf-8")).hexdigest()
        lines.append(f"{path}\t{digest}")
    raw = "\n".join(lines).encode("utf-8")
    return sha256(raw).hexdigest()


def compute_inventory_hash(items: Iterable[str]) -> str:
    entries = [str(x).strip() for x in items if str(x).strip()]
    raw = "\n".join(sorted(entries)).encode("utf-8")
    return sha256(raw).hexdigest()


def parse_selector_output(text: str, inventory: Iterable[str]) -> list[str]:
    inventory_set = {str(x).strip() for x in inventory if str(x).strip()}
    picks: list[str] = []
    if not text:
        return picks
    for line in str(text).splitlines():
        candidate = line.strip()
        if candidate.startswith(("-", "*")):
            candidate = candidate[1:].strip()
        if not candidate or candidate not in inventory_set:
            continue
        if candidate not in picks:
            picks.append(candidate)
    return picks


__all__ = [
    "ANCHOR_FILE_PATTERNS",
    "ChildStat",
    "is_anchor_filename",
    "build_inventory_list",
    "build_child_stats",
    "build_tree_snapshot",
    "format_evidence_blocks",
    "format_anchor_blocks",
    "compute_inputs_hash",
    "compute_inventory_hash",
    "parse_selector_output",
]
