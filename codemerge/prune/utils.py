from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, TextIO
import ast
import os

from codemerge.filemerger.render import escape_file_display, format_label

@dataclass
class DefSpan:
    """Span metadata for a definition discovered in Python source."""

    kind: str
    name: str
    qualname: str
    start: int
    end: int
    docfirst: str | None


def list_python_defs(path: str) -> List[DefSpan]:
    """Return all defs/classes in ``path`` with basic metadata.

    The function ignores files that cannot be read or parsed.
    """

    try:
        src = Path(path).read_text(encoding="utf-8")
    except Exception:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    spans: List[DefSpan] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, prefix: List[str] | None = None) -> None:
            self.prefix = prefix or []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
            qual = ".".join(self.prefix + [node.name])
            doc = ast.get_docstring(node)
            first = doc.splitlines()[0] if doc else None
            start = node.lineno
            if node.decorator_list:
                start = min(dec.lineno for dec in node.decorator_list)
            spans.append(
                DefSpan("def", node.name, qual, start, node.end_lineno or node.lineno, first)
            )

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
            qual = ".".join(self.prefix + [node.name])
            doc = ast.get_docstring(node)
            first = doc.splitlines()[0] if doc else None
            start = node.lineno
            if node.decorator_list:
                start = min(dec.lineno for dec in node.decorator_list)
            spans.append(
                DefSpan("class", node.name, qual, start, node.end_lineno or node.lineno, first)
            )
            Visitor(self.prefix + [node.name]).generic_visit(node)

    Visitor().visit(tree)
    return spans


def prune_python_defs(
    path: str,
    keep: Set[str],
    drop: Set[str],
    in_place: bool = False,
) -> str:
    """Return source of ``path`` with selected defs/classes removed."""

    try:
        src = Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""

    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        text = src
        if in_place:
            Path(path).write_text(text, encoding="utf-8")
        return text

    spans: dict[str, dict] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self, prefix: List[str] | None = None) -> None:
            self.prefix = prefix or []

        def _qual(self, name: str) -> str:
            return ".".join(self.prefix + [name])

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
            qual = self._qual(node.name)
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            spans[qual] = {
                "start": start,
                "end": node.end_lineno or node.lineno,
                "kind": "class",
                "parent": ".".join(self.prefix) if self.prefix else None,
            }
            self.prefix.append(node.name)
            self.generic_visit(node)
            self.prefix.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
            qual = self._qual(node.name)
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            spans[qual] = {
                "start": start,
                "end": node.end_lineno or node.lineno,
                "kind": "def",
                "parent": ".".join(self.prefix) if self.prefix else None,
            }
            self.prefix.append(node.name)
            self.generic_visit(node)
            self.prefix.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)

    to_remove: List[tuple[int, int]] = []

    class_drops = set()
    for qual, info in spans.items():
        if info["kind"] != "class":
            continue
        if qual in drop and qual not in keep:
            if any(k == qual or k.startswith(qual + ".") for k in keep):
                continue
            class_drops.add(qual)
            to_remove.append((info["start"], info["end"]))

    for qual, info in spans.items():
        if info["kind"] == "class":
            continue
        if qual in drop and qual not in keep and info["parent"] not in class_drops:
            to_remove.append((info["start"], info["end"]))

    if not to_remove:
        text = src
        if in_place:
            Path(path).write_text(text, encoding="utf-8")
        return text

    for i, (start, end) in enumerate(to_remove):
        while end < len(lines) and not lines[end].strip():
            end += 1
        to_remove[i] = (start, end)

    new_lines = lines[:]
    for start, end in sorted(to_remove, reverse=True):
        del new_lines[start - 1 : end]

    text = "\n".join(new_lines)
    if new_lines and not text.endswith("\n"):
        text += "\n"
    if in_place:
        Path(path).write_text(text, encoding="utf-8")
    return text


def slice_lines(path: str, start: int, end: int) -> str:
    """Return lines [start, end] from ``path`` including a trailing newline."""

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    start = max(1, start)
    end = max(start, end)
    return "\n".join(lines[start - 1 : end]) + "\n"


def merge_selected_spans(
    spans: List[dict],
    root: str,
    out_file: str,
    start_label: str,
    end_label: str,
    *,
    fh: TextIO | None = None,
    append: bool = False,
    enumerate_lines: bool = False,
    remove_empty: bool = False,
) -> None:
    """Write extracted spans to ``out_file`` (or ``fh`` when provided)."""

    if not spans:
        return

    def _transform(text: str) -> str:
        lines = text.splitlines()
        if remove_empty:
            lines = [ln for ln in lines if ln.strip()]
        if enumerate_lines:
            width = len(str(len(lines))) if lines else 1
            lines = [f"{i + 1:>{width}}: {ln}" for i, ln in enumerate(lines)]
        return "\n".join(lines)

    def _write(handle: TextIO) -> None:
        for sp in spans:
            segment = slice_lines(sp["path"], sp["start"], sp["end"])
            segment = _transform(segment)
            if segment and not segment.endswith("\n"):
                segment += "\n"
            rel_path = os.path.relpath(sp["path"], root)
            display = f"{rel_path}  # lines {sp['start']}-{sp['end']}"
            file_display = f"'{escape_file_display(display)}'"
            handle.write(format_label(start_label, file_display))
            handle.write("\n")
            if sp.get("why"):
                handle.write(f"# why: {sp['why']}\n")
            handle.write(segment)
            handle.write(format_label(end_label, file_display))
            handle.write("\n\n")

    if fh is not None:
        _write(fh)
    else:
        mode = "a" if append else "w"
        with open(out_file, mode, encoding="utf-8") as handle:
            _write(handle)


__all__ = [
    "DefSpan",
    "list_python_defs",
    "prune_python_defs",
    "slice_lines",
    "merge_selected_spans",
]
