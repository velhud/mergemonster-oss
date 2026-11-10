from __future__ import annotations

import ast
from pathlib import Path

from .models import StructuralFileView, StructuralSymbol


def _signature(source_lines: list[str], node: ast.AST) -> str:
    start = max(int(getattr(node, "lineno", 1)) - 1, 0)
    end = max(int(getattr(node, "end_lineno", getattr(node, "lineno", 1))) - 1, start)
    collected: list[str] = []
    for index in range(start, min(end + 1, len(source_lines))):
        line = source_lines[index]
        collected.append(line.rstrip())
        if line.rstrip().endswith(":"):
            break
    return "\n".join(collected).rstrip()


def _decorators(source_lines: list[str], node: ast.AST) -> list[str]:
    out: list[str] = []
    for deco in getattr(node, "decorator_list", []) or []:
        lineno = int(getattr(deco, "lineno", 0) or 0)
        if 1 <= lineno <= len(source_lines):
            out.append(source_lines[lineno - 1].rstrip())
    return out


def _import_line(source_lines: list[str], node: ast.AST) -> str:
    lineno = int(getattr(node, "lineno", 0) or 0)
    end = int(getattr(node, "end_lineno", lineno) or lineno)
    if lineno <= 0:
        return ""
    return "\n".join(source_lines[lineno - 1 : end]).rstrip()


def _constant_line(source_lines: list[str], node: ast.AST) -> str | None:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return None
    lineno = int(getattr(node, "lineno", 0) or 0)
    if lineno <= 0 or lineno > len(source_lines):
        return None
    line = source_lines[lineno - 1].rstrip()
    stripped = line.strip()
    if not stripped or stripped.startswith("_"):
        return None
    if isinstance(node, ast.Assign):
        targets = [getattr(t, "id", "") for t in node.targets if isinstance(t, ast.Name)]
        if not targets:
            return None
        if not all(name.isupper() for name in targets if name):
            return None
    elif isinstance(node, ast.AnnAssign):
        target = getattr(node.target, "id", "")
        if not target or not target.isupper():
            return None
    return line


class _SkeletonBuilder(ast.NodeVisitor):
    def __init__(self, rel_path: str, source_lines: list[str]) -> None:
        self.rel_path = rel_path
        self.source_lines = source_lines
        self.lines: list[str] = []
        self.imports: list[str] = []
        self.symbols: list[StructuralSymbol] = []
        self.parents: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # type: ignore[override]
        line = _import_line(self.source_lines, node)
        if line:
            self.imports.append(line)
            self.lines.append(line)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # type: ignore[override]
        line = _import_line(self.source_lines, node)
        if line:
            self.imports.append(line)
            self.lines.append(line)

    def visit_Assign(self, node: ast.Assign) -> None:  # type: ignore[override]
        line = _constant_line(self.source_lines, node)
        if line:
            self.lines.append(line)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # type: ignore[override]
        line = _constant_line(self.source_lines, node)
        if line:
            self.lines.append(line)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
        self._emit_symbol(node, "class")
        self.parents.append(node.name)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.visit(child)
        self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
        self._emit_symbol(node, "method" if self.parents else "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def _emit_symbol(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
    ) -> None:
        indent = "    " * len(self.parents)
        for decorator in _decorators(self.source_lines, node):
            self.lines.append(f"{indent}{decorator.strip()}")
        signature = _signature(self.source_lines, node)
        if signature:
            signature_lines = signature.splitlines()
            self.lines.append(f"{indent}{signature_lines[0].strip()}")
            for cont in signature_lines[1:]:
                self.lines.append(f"{indent}{cont.strip()}")
        doc = ast.get_docstring(node)
        if doc:
            first = doc.strip().splitlines()[0].strip()
            if first:
                self.lines.append(f'{indent}    """{first}"""')
        self.lines.append(f"{indent}    ...")
        self.symbols.append(
            StructuralSymbol(
                name=node.name,
                kind=kind,
                file_rel_path=self.rel_path,
                language="python",
                start_line=int(getattr(node, "lineno", 1) or 1),
                end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1)) or 1),
                signature=signature or None,
                doc=doc,
                exported=not node.name.startswith("_"),
                parent_name=self.parents[-1] if self.parents else None,
            )
        )


def build_python_ast_view(
    *,
    path: str | Path,
    rel_path: str,
    content: str,
    source_sha256: str,
    extractor_fingerprint: str,
    token_counter,
) -> StructuralFileView:
    source_lines = content.splitlines()
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return StructuralFileView(
            rel_path=rel_path,
            language="python",
            parser_status="error",
            imports=(),
            exports=(),
            symbols=(),
            skeleton_text="",
            token_count_source=token_counter(content),
            token_count_skeleton=0,
            compression_ratio=None,
            source_sha256=source_sha256,
            extractor_fingerprint=extractor_fingerprint,
            warnings=(f"Python syntax error: {exc.msg}",),
        )

    builder = _SkeletonBuilder(rel_path, source_lines)
    for node in tree.body:
        builder.visit(node)
    skeleton = "\n".join(line for line in builder.lines if line.strip()).strip()
    if skeleton:
        skeleton += "\n"
    source_tokens = token_counter(content)
    skeleton_tokens = token_counter(skeleton) if skeleton else 0
    ratio = None
    if source_tokens:
        ratio = 1.0 - (float(skeleton_tokens) / float(source_tokens))
    return StructuralFileView(
        rel_path=rel_path,
        language="python",
        parser_status="ok",
        imports=tuple(dict.fromkeys(builder.imports)),
        exports=tuple(sym.name for sym in builder.symbols if sym.exported),
        symbols=tuple(builder.symbols),
        skeleton_text=skeleton,
        token_count_source=source_tokens,
        token_count_skeleton=skeleton_tokens,
        compression_ratio=ratio,
        source_sha256=source_sha256,
        extractor_fingerprint=extractor_fingerprint,
        warnings=(),
    )
