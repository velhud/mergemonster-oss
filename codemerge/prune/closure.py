from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Iterable
import ast


@dataclass
class Span:
    path: str
    start: int
    end: int
    qualname: str
    kind: str
    why: str  # "pick" | "same_file_ref" | "import_attr" | "package_glue"
    role: str = "seed"


def _parse_for_refs_and_top(path: str) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """Return (defs, top) like pruner._build_prune_plan's local parser."""
    info: Dict[str, Dict] = {"defs": {}, "refs": {}}
    top: Dict[str, Dict] = {}
    try:
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        return info, top

    def _collect_names(node: ast.AST) -> set:
        names: set = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                names.add(n.id)
            elif isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                base = n.value
                if isinstance(base, ast.Name):
                    names.add((base.id, n.attr))
        return names

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[str] = []

        def _qual(self, name: str) -> str:
            return ".".join(self.stack + [name])

        def visit_ClassDef(self, n: ast.ClassDef):  # type: ignore[override]
            q = self._qual(n.name)
            info["defs"][q] = {"start": n.lineno, "end": getattr(n, "end_lineno", n.lineno), "kind": "class"}
            info["refs"][q] = _collect_names(n)
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        def visit_FunctionDef(self, n: ast.FunctionDef):  # type: ignore[override]
            q = self._qual(n.name)
            info["defs"][q] = {"start": n.lineno, "end": getattr(n, "end_lineno", n.lineno), "kind": "def"}
            info["refs"][q] = _collect_names(n)
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, n: ast.Assign):  # type: ignore[override]
            if self.stack:
                return
            names = [t.id for t in n.targets if isinstance(t, ast.Name)]
            if len(names) == 1:
                top[names[0]] = {
                    "start": n.lineno,
                    "end": getattr(n, "end_lineno", n.lineno),
                    "kind": "const",
                    "qualname": names[0],
                }

    V().visit(tree)
    # register top-level defs, too
    for q, d in info["defs"].items():
        if "." not in q:
            top[q] = {"start": d["start"], "end": d["end"], "kind": d["kind"], "qualname": q}
    return info, top


def _module_to_repo_path(index: Dict[str, Dict], root: str, dotted: str) -> Optional[str]:
    """Map 'pkg.mod' -> repo file path using index; supports __init__.py."""
    want_py = dotted.replace(".", "/") + ".py"
    want_pkg = dotted.replace(".", "/") + "/__init__.py"
    rroot = Path(root).resolve()
    for path in index.keys():
        try:
            rel = Path(path).resolve().relative_to(rroot).as_posix()
        except Exception:
            continue
        if rel == want_py or rel == want_pkg:
            return path
    return None


def _resolve_import_attr(index: Dict[str, Dict], file_path: str, module: str, attr: str, root: str) -> Tuple[Optional[str], Optional[dict]]:
    """Return (target_file, target_def) when an imported module's attribute is referenced."""
    info = index.get(file_path, {})
    target_file: Optional[str] = None
    # direct imports from this file
    for imp in info.get("imports", []) or []:
        if imp.get("module") == module:
            # resolve relative base by import level
            level = int(imp.get("level", 0))
            base = Path(file_path).parent
            for _ in range(max(level - 1, 0)):
                base = base.parent
            probe = base / module.replace(".", "/")
            for c in (probe.with_suffix(".py"), probe / "__init__.py"):
                if c.exists():
                    target_file = str(c)
                    break
            break
    # fallback by scanning index modules
    if not target_file:
        target_file = _module_to_repo_path(index, root, module)

    if not target_file:
        return None, None

    # locate the attr inside that module
    defs = index.get(target_file, {}).get("defs", []) or []
    for d in defs:
        if d.get("name") == attr:
            return target_file, d
    return target_file, None  # keep whole file as supportive if attr span not found


def build_minimal(
    root: str,
    picks: List[dict],
    index: Dict[str, Dict],
    *,
    keep_class_for_method: bool = False,
    allowed_paths: Optional[Iterable[str]] = None,
) -> Tuple[List[Span], List[Span]]:
    """
    Return (tier0, tier1) minimal closure.
    Tier‑0 = essential spans (selected + their minimal same‑file deps + resolved import attrs).
    Tier‑1 = supportive (same‑file helpers indirectly used; file‑level imports when attr span unknown; __init__/__all__ glue).
    """
    ap: Optional[set[str]] = None
    if allowed_paths:
        ap = {str(Path(p).resolve()) for p in allowed_paths}

    tier0: list[Span] = []
    tier1: list[Span] = []
    seen0: set[tuple[str, str]] = set()
    seen1: set[tuple[str, str]] = set()

    for it in picks:
        p = it["file_path"]
        q = it["qualname"]
        if ap and str(Path(p).resolve()) not in ap:  # ignore out-of-scope
            continue
        if q == "<FILE>":
            fk = (p, "<FILE>")
            if fk not in seen1:
                reason = str(it.get("reason", "pick_file"))
                role = str(it.get("role", "seed"))
                tier1.append(Span(p, 1, 10**9, "<FILE>", "file", reason, role))
                seen1.add(fk)
            continue

        info, top = _parse_for_refs_and_top(p)
        d = info["defs"].get(q)
        if not d:
            continue

        # Possibly upsize a method to its class skeleton (selection stays a class span)
        qual = q
        if (not keep_class_for_method) and ("." in q):
            parts = q.split(".")
            # choose nearest class ancestor
            for i in range(len(parts) - 1, 0, -1):
                cq = ".".join(parts[:i])
                dd = info["defs"].get(cq)
                if dd and dd.get("kind") == "class":
                    qual = cq
                    d = dd
                    break

        k = (p, qual)
        if k not in seen0:
            reason = str(it.get("reason", "pick"))
            role = str(it.get("role", "seed"))
            tier0.append(Span(p, d["start"], d["end"], qual, d["kind"], reason, role))
            seen0.add(k)

        # same-file top-level refs
        for name in info["refs"].get(qual, set()):
            if isinstance(name, tuple):
                continue
            ref = top.get(name)
            if not ref:
                continue
            rk = (p, ref["qualname"])
            if rk in seen0 or rk in seen1:
                continue
            sp = Span(p, ref["start"], ref["end"], ref["qualname"], ref["kind"], "same_file_ref", "seed")
            # constants and same-file helpers -> Tier‑0 because they’re directly used
            tier0.append(sp)
            seen0.add(rk)

        # resolve imported attributes (module.attr)
        for ref in info["refs"].get(qual, set()):
            if not isinstance(ref, tuple):  # skip name-only
                continue
            mod, attr = ref
            tgt_file, tgt_def = _resolve_import_attr(index, p, mod, attr, root)
            if not tgt_file:
                continue
            if ap and str(Path(tgt_file).resolve()) not in ap:
                continue
            if tgt_def:
                rk = (tgt_file, tgt_def.get("qualname", ""))
                if rk not in seen0:
                    tier0.append(
                        Span(
                            tgt_file,
                            tgt_def["start"],
                            tgt_def["end"],
                            tgt_def["qualname"],
                            tgt_def["kind"],
                            "import_attr",
                            "neighbor",
                        )
                    )
                    seen0.add(rk)
            else:
                fk = (tgt_file, "<FILE>")
                if fk not in seen1:
                    # keep whole module as supportive when we cannot resolve an attr span
                    # Writer will include it as a file.
                    tier1.append(Span(tgt_file, 1, 10**9, "<FILE>", "file", "import_attr", "neighbor"))
                    seen1.add(fk)

    return tier0, tier1
