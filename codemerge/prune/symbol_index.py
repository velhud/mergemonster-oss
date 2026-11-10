

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from ..paths import state_directory
from typing import Dict, List
import ast
import json

from ..filemerger import FileProcessor
from ..logger import get_logger, log_event
from ..models import ScanContext



CACHE_FILE = state_directory() / "codemerge_symbols.json"

logger = get_logger(__name__)


@dataclass
class DefSpan:
    kind: str
    name: str
    qualname: str
    start: int
    end: int
    doc: str | None


def _python_defs(path: str) -> List[DefSpan]:

    try:
        source = Path(path).read_text(encoding="utf-8")
    except Exception:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    spans: List[DefSpan] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, prefix: List[str] | None = None) -> None:
            self.prefix = prefix or []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
            qual = ".".join(self.prefix + [node.name])
            doc = ast.get_docstring(node)
            spans.append(
                DefSpan("def", node.name, qual, node.lineno, node.end_lineno or node.lineno, doc)
            )

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
            qual = ".".join(self.prefix + [node.name])
            doc = ast.get_docstring(node)
            spans.append(
                DefSpan("class", node.name, qual, node.lineno, node.end_lineno or node.lineno, doc)
            )
            Visitor(self.prefix + [node.name]).generic_visit(node)

    Visitor().visit(tree)
    return spans


def _python_refs(path: str) -> Dict[str, List[str | tuple[str, str]]]:

    try:
        source = Path(path).read_text(encoding="utf-8")
    except Exception:
        return {}

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    refs: Dict[str, set[str | tuple[str, str]]] = {}

    def collect(node: ast.AST, stop_nested: bool) -> set[str | tuple[str, str]]:
        names: set[str | tuple[str, str]] = set()

        class NameVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, n: ast.FunctionDef) -> None:  # type: ignore[override]
                if stop_nested:
                    return
                self.generic_visit(n)

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, n: ast.ClassDef) -> None:  # type: ignore[override]
                if stop_nested:
                    return
                self.generic_visit(n)

            def visit_Name(self, n: ast.Name) -> None:  # type: ignore[override]
                if isinstance(n.ctx, ast.Load):
                    names.add(n.id)

            def visit_Attribute(self, n: ast.Attribute) -> None:  # type: ignore[override]
                if isinstance(n.ctx, ast.Load) and isinstance(n.value, ast.Name):
                    names.add((n.value.id, n.attr))
                self.generic_visit(n)

        visitor = NameVisitor()
        for child in getattr(node, "body", []):
            visitor.visit(child)
        return names

    class Visitor(ast.NodeVisitor):
        def __init__(self, prefix: List[str] | None = None) -> None:
            self.prefix = prefix or []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
            qual = ".".join(self.prefix + [node.name])
            refs[qual] = collect(node, stop_nested=True)
            Visitor(self.prefix + [node.name]).generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
            qual = ".".join(self.prefix + [node.name])
            refs[qual] = collect(node, stop_nested=False)
            Visitor(self.prefix + [node.name]).generic_visit(node)

    Visitor().visit(tree)

    def _sort_key(x: str | tuple[str, str]) -> tuple:
        if isinstance(x, tuple):
            return (1, x[0], x[1])
        return (0, x)

    return {k: sorted(v, key=_sort_key) for k, v in refs.items()}


def _python_imports(path: str) -> List[Dict[str, object]]:

    try:
        source = Path(path).read_text(encoding="utf-8")
    except Exception:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    names: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            if node.module:
                names.add((node.module, level))
            else:
                for alias in node.names:
                    names.add((alias.name, level))

    return [
        {"module": name, "level": lvl} for name, lvl in sorted(names)
    ]


def build_symbol_index(
    ctx: ScanContext,
    fp: FileProcessor,
    existing: Dict[str, Dict] | None = None,
) -> Dict[str, Dict]:

    log_event(logger, "index_build_start", files=len(ctx.files))

    index: Dict[str, Dict] = dict(existing or {})
    ctx_paths: set[str] = set()
    for fm in ctx.files:
        path = str(fm.abs_path)
        ctx_paths.add(path)
        cached = index.get(path)
        if (
            cached
            and cached.get("mtime") == fm.mtime
            and cached.get("size") == fm.size_bytes
        ):
            # Reuse cached information including token count and parsed symbols
            cached.setdefault("refs", {})
            continue

        info: Dict = {
            "lang": fm.ext,
            "defs": [],
            "imports": [],
            "refs": {},
            "tokens": fm.token_count,
            "mtime": fm.mtime,
            "size": fm.size_bytes,
        }
        if fm.ext == "py":
            info["defs"] = [asdict(d) for d in _python_defs(path)]
            info["imports"] = _python_imports(path)
            info["refs"] = _python_refs(path)
        index[path] = info

    # Drop entries for files no longer present
    stale = set(index.keys()) - ctx_paths
    for path in stale:
        del index[path]

    def _stats(idx: Dict[str, Dict]) -> tuple[int, int]:
        defs = imports = 0
        for info in idx.values():
            defs += len(info.get("defs", []) or [])
            imports += len(info.get("imports", []) or [])
        return defs, imports

    defs, imports = _stats(index)
    log_event(
        logger,
        "index_build_end",
        files=len(index),
        defs=defs,
        imports=imports,
    )
    return index


def _index_stale(ctx: ScanContext, index: Dict[str, Dict]) -> tuple[bool, Dict[str, int]]:

    ctx_paths = {str(fm.abs_path) for fm in ctx.files}
    reasons = {
        "missing": 0,
        "changed_mtime": 0,
        "changed_size": 0,
        "extra_entries": 0,
    }

    for fm in ctx.files:
        path = str(fm.abs_path)
        cached = index.get(path)
        if not cached:
            if fm.ext and fm.ext not in {"py"}:
                continue
            reasons["missing"] += 1
            continue
        if cached.get("mtime") != fm.mtime:
            reasons["changed_mtime"] += 1
        if cached.get("size") != fm.size_bytes:
            reasons["changed_size"] += 1

    for path in index.keys():
        if path not in ctx_paths:
            reasons["extra_entries"] += 1

    stale = any(reasons.values())
    log_event(logger, "index_stale_check", stale=stale, reasons=reasons)
    return stale, reasons


def save_symbol_index(root: str, index: Dict[str, Dict]) -> None:
    data = {"root": root, "index": index}
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def load_symbol_index(root: str) -> Dict[str, Dict] | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("root") != root:
        return None
    return data.get("index")
