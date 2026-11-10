from __future__ import annotations
from typing import Dict, List, Tuple, Optional
import ast
from pathlib import Path

from ..llm.prune import synthesize_harness_cases


def _find_zero_arg_funcs(path: str) -> List[Tuple[str, str]]:
    """Return [(module_import, qualname)] for callables with no required positional args."""
    try:
        src = open(path, "r", encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        return []
    out: list[Tuple[str, str]] = []

    class St(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[str] = []

        def _qual(self, n: str) -> str:
            return ".".join(self.stack + [n])

        def visit_ClassDef(self, n: ast.ClassDef):
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        def visit_FunctionDef(self, n: ast.FunctionDef):
            # zero required positional args: ignore self for methods
            args = n.args.args
            required = len(args) - (1 if self.stack else 0)
            if required == 0:
                out.append(self._qual(n.name))
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    St().visit(tree)
    # caller will prefix with module import name
    return [(None, q) for q in out]  # type: ignore


def synthesize(
    goal: str,
    root: str,
    tier0: List[dict],
    *,
    client: Optional[object],
    cfg: Dict,
) -> List[str]:
    """
    Return a list of small Python scripts (as strings) to execute.
    Preferred path: LLM proposes calls. Fallback: import + smoke‑call zero‑arg funcs.
    """
    # Prepare compact index for LLM
    files = sorted({sp["path"] for sp in tier0 if sp.get("path") and sp["path"].endswith(".py")})
    options = []
    for p in files:
        try:
            qnames = _find_zero_arg_funcs(p)
        except Exception:
            qnames = []
        options.append({"path": p, "zero_arg_candidates": [q for _, q in qnames]})

    scripts = synthesize_harness_cases(goal=goal, options=options, client=client, cfg=cfg)

    if not scripts:
        # Fallback: smoke import + call zero‑arg functions
        lines = ["import importlib, traceback"]
        for p in files:
            dotted = _path_to_module(root, p)
            lines.append(f"m=importlib.import_module({dotted!r})")
            for _, q in _find_zero_arg_funcs(p):
                lines.append(f"try:\n    _=eval({q!r}, vars(m)) if '(' in {q!r} else getattr(m, {q!r})()\nexcept Exception:\n    traceback.print_exc()")
        scripts = ["\n".join(lines)]
    return scripts


def _path_to_module(root: str, path: str) -> str:
    rel = str(Path(path).resolve().relative_to(Path(root).resolve()).as_posix())
    parts = rel.split("/")
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join([p for p in parts if p])
