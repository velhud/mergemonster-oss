from __future__ import annotations

import re
from pathlib import Path

from .models import StructuralFileView, StructuralSymbol


TREE_SITTER_LANGUAGES = {"python", "javascript", "typescript", "go"}
FALLBACK_LANGUAGES = {"javascript", "typescript", "go"}
_MAX_SIGNATURE_LEN = 220


def build_tree_sitter_view(
    *,
    path: str | Path,
    rel_path: str,
    content: str,
    language: str,
    source_sha256: str,
    extractor_fingerprint: str,
    token_counter,
) -> StructuralFileView | None:
    """Optional Tree-sitter hook plus deterministic fallback extractors.

    MergeMonster keeps this adapter local so production code never imports donor
    repositories. Python uses the AST adapter first. JavaScript, TypeScript,
    and Go use deterministic line-level fallback extraction unless compatible
    Tree-sitter parser packages are available in a later runtime.
    """

    if language not in TREE_SITTER_LANGUAGES:
        return None
    if language == "python":
        return None

    if language in {"javascript", "typescript"}:
        return _build_js_ts_fallback_view(
            rel_path=rel_path,
            content=content,
            language=language,
            source_sha256=source_sha256,
            extractor_fingerprint=extractor_fingerprint,
            token_counter=token_counter,
        )
    if language == "go":
        return _build_go_fallback_view(
            rel_path=rel_path,
            content=content,
            source_sha256=source_sha256,
            extractor_fingerprint=extractor_fingerprint,
            token_counter=token_counter,
        )
    return None


def _clip(text: str, limit: int = _MAX_SIGNATURE_LEN) -> str:
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 4].rstrip() + " ..."


def _source_metrics(
    *,
    rel_path: str,
    language: str,
    skeleton_lines: list[str],
    imports: list[str],
    exports: list[str],
    symbols: list[StructuralSymbol],
    content: str,
    source_sha256: str,
    extractor_fingerprint: str,
    token_counter,
    warnings: tuple[str, ...],
) -> StructuralFileView:
    skeleton = "\n".join(line.rstrip() for line in skeleton_lines if line.strip()).strip()
    if skeleton:
        skeleton += "\n"
    source_tokens = token_counter(content)
    skeleton_tokens = token_counter(skeleton) if skeleton else 0
    ratio = 1.0 - (float(skeleton_tokens) / float(source_tokens)) if source_tokens else None
    return StructuralFileView(
        rel_path=rel_path,
        language=language,
        parser_status="fallback",
        imports=tuple(dict.fromkeys(imports)),
        exports=tuple(dict.fromkeys(exports)),
        symbols=tuple(symbols),
        skeleton_text=skeleton,
        token_count_source=source_tokens,
        token_count_skeleton=skeleton_tokens,
        compression_ratio=ratio,
        source_sha256=source_sha256,
        extractor_fingerprint=extractor_fingerprint,
        warnings=warnings,
    )


def _brace_delta(text: str) -> int:
    in_string: str | None = None
    escaped = False
    delta = 0
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if in_string:
            if char == in_string:
                in_string = None
            continue
        if char in {"'", '"', "`"}:
            in_string = char
            continue
        if char == "{":
            delta += 1
        elif char == "}":
            delta -= 1
    return delta


def _collect_statement(lines: list[str], start: int) -> tuple[str, int]:
    parts: list[str] = []
    depth = 0
    index = start
    while index < len(lines):
        raw = lines[index].rstrip()
        parts.append(raw.strip())
        depth += _brace_delta(raw)
        stripped = raw.strip()
        if index > start and depth <= 0:
            break
        if depth <= 0 and (stripped.endswith(";") or stripped.endswith("}") or "{" in stripped):
            break
        if index == start and re.search(r"\)\s*(?::\s*[^={]+)?\s*=>", stripped):
            break
        index += 1
    return " ".join(part for part in parts if part), index


def _collect_paren_or_brace_statement(lines: list[str], start: int) -> tuple[str, int]:
    parts: list[str] = []
    brace_depth = 0
    paren_depth = 0
    index = start
    while index < len(lines):
        raw = lines[index].rstrip()
        stripped = raw.strip()
        parts.append(stripped)
        brace_depth += _brace_delta(raw)
        paren_depth += raw.count("(") - raw.count(")")
        if index > start and brace_depth <= 0 and paren_depth <= 0:
            break
        if index == start and brace_depth <= 0 and paren_depth <= 0 and (
            stripped.endswith(";") or stripped.endswith("}") or not stripped.endswith("(")
        ):
            break
        index += 1
    return " ".join(part for part in parts if part), index


def _signature_with_body_marker(statement: str, language: str) -> str:
    text = _clip(statement)
    if "{" in text:
        return _clip(text.split("{", 1)[0].rstrip() + " { ... }")
    if language in {"javascript", "typescript"} and "=>" in text:
        return _clip(text.split("=>", 1)[0].rstrip() + "=> ...")
    return text


def _pending_doc(lines: list[str], index: int, prefix: str) -> list[str]:
    docs: list[str] = []
    cursor = index - 1
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped:
            if docs:
                break
            cursor -= 1
            continue
        if stripped.startswith(prefix) or stripped.startswith("/**") or stripped.startswith("*") or stripped.endswith("*/"):
            docs.append(lines[cursor].rstrip())
            cursor -= 1
            continue
        break
    docs.reverse()
    return docs[-4:]


def _add_symbol(
    symbols: list[StructuralSymbol],
    *,
    name: str,
    kind: str,
    rel_path: str,
    language: str,
    line: int,
    signature: str,
    parent_name: str | None = None,
) -> None:
    symbols.append(
        StructuralSymbol(
            name=name,
            kind=kind,
            file_rel_path=rel_path,
            language=language,
            start_line=line,
            end_line=line,
            signature=signature,
            exported=name[:1].isupper() or signature.strip().startswith("export "),
            parent_name=parent_name,
        )
    )


def _extract_js_class_members(
    lines: list[str],
    start: int,
    *,
    rel_path: str,
    language: str,
    symbols: list[StructuralSymbol],
    class_name: str,
) -> tuple[list[str], int]:
    header_statement, header_end = _collect_statement(lines, start)
    header = _clip(header_statement.split("{", 1)[0].rstrip() + " {")
    out = [header]
    depth = 0
    index = start
    if "{" not in lines[start]:
        index = header_end
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        previous_depth = depth
        depth += _brace_delta(raw)
        if index > start and previous_depth == 1:
            method_match = re.match(
                r"^(?:(?:public|private|protected|static|override|async|get|set|readonly)\s+)*([A-Za-z_$][\w$]*|constructor)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*(?::\s*[^={]+)?\s*\{",
                stripped,
            )
            if method_match and not stripped.startswith(("if ", "for ", "while ", "switch ", "catch ")):
                signature = _signature_with_body_marker(stripped, language)
                out.append(f"    {signature}")
                _add_symbol(
                    symbols,
                    name=method_match.group(1),
                    kind="method",
                    rel_path=rel_path,
                    language=language,
                    line=index + 1,
                    signature=signature,
                    parent_name=class_name,
                )
            elif re.match(
                r"^(?:(?:public|private|protected|static|readonly)\s+)*[A-Za-z_$][\w$]*\s*(?::[^=;]+)?(?:=[^;{}]+)?;",
                stripped,
            ):
                out.append(f"    {_clip(stripped)}")
        if index > start and depth <= 0:
            out.append("}")
            return out, index
        index += 1
    out.append("}")
    return out, max(start, index - 1)


def _build_js_ts_fallback_view(
    *,
    rel_path: str,
    content: str,
    language: str,
    source_sha256: str,
    extractor_fingerprint: str,
    token_counter,
) -> StructuralFileView:
    lines = content.splitlines()
    skeleton: list[str] = []
    imports: list[str] = []
    exports: list[str] = []
    symbols: list[StructuralSymbol] = []
    emitted_docs: set[int] = set()

    def emit_docs(index: int) -> None:
        docs = _pending_doc(lines, index, "//")
        for doc in docs:
            line_no = lines.index(doc) if doc in lines else -1
            if line_no not in emitted_docs:
                skeleton.append(doc)
                emitted_docs.add(line_no)

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("@"):
            skeleton.append(stripped)
            index += 1
            continue

        if re.match(r"^(import|export\s+.+\s+from)\b", stripped):
            statement, index = _collect_statement(lines, index)
            imports.append(statement)
            skeleton.append(_clip(statement))
            index += 1
            continue

        class_match = re.match(r"^(export\s+)?(default\s+)?(abstract\s+)?class\s+([A-Za-z_$][\w$]*)", stripped)
        if class_match:
            emit_docs(index)
            name = class_match.group(4)
            class_lines, end = _extract_js_class_members(
                lines,
                index,
                rel_path=rel_path,
                language=language,
                symbols=symbols,
                class_name=name,
            )
            signature = class_lines[0]
            exports.append(name) if class_match.group(1) else None
            skeleton.extend(class_lines)
            _add_symbol(symbols, name=name, kind="class", rel_path=rel_path, language=language, line=index + 1, signature=signature)
            index = end + 1
            continue

        iface_match = re.match(r"^(export\s+)?(interface|type|enum)\s+([A-Za-z_$][\w$]*)", stripped)
        if iface_match:
            emit_docs(index)
            statement, end = _collect_statement(lines, index)
            name = iface_match.group(3)
            exports.append(name) if iface_match.group(1) else None
            signature = _clip(statement)
            skeleton.append(signature)
            _add_symbol(symbols, name=name, kind=iface_match.group(2), rel_path=rel_path, language=language, line=index + 1, signature=signature)
            index = end + 1
            continue

        fn_match = re.match(r"^(export\s+)?(default\s+)?(async\s+)?function\s+\*?\s*([A-Za-z_$][\w$]*)", stripped)
        if fn_match:
            emit_docs(index)
            statement, end = _collect_statement(lines, index)
            name = fn_match.group(4)
            exports.append(name) if fn_match.group(1) else None
            signature = _signature_with_body_marker(statement, language)
            skeleton.append(signature)
            _add_symbol(symbols, name=name, kind="function", rel_path=rel_path, language=language, line=index + 1, signature=signature)
            index = end + 1
            continue

        arrow_match = re.match(
            r"^(export\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(async\s*)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
            stripped,
        )
        if arrow_match:
            emit_docs(index)
            statement, end = _collect_statement(lines, index)
            name = arrow_match.group(3)
            exports.append(name) if arrow_match.group(1) else None
            signature = _signature_with_body_marker(statement, language)
            skeleton.append(signature)
            _add_symbol(symbols, name=name, kind="function", rel_path=rel_path, language=language, line=index + 1, signature=signature)
            index = end + 1
            continue

        const_match = re.match(r"^(export\s+)?(const|let|var)\s+([A-Z][A-Z0-9_]*)\b", stripped)
        if const_match and len(stripped) <= _MAX_SIGNATURE_LEN and "{" not in stripped:
            name = const_match.group(3)
            exports.append(name) if const_match.group(1) else None
            skeleton.append(stripped)
            _add_symbol(symbols, name=name, kind="constant", rel_path=rel_path, language=language, line=index + 1, signature=stripped)
        index += 1

    return _source_metrics(
        rel_path=rel_path,
        language=language,
        skeleton_lines=skeleton,
        imports=imports,
        exports=exports,
        symbols=symbols,
        content=content,
        source_sha256=source_sha256,
        extractor_fingerprint=extractor_fingerprint,
        token_counter=token_counter,
        warnings=("Tree-sitter parser unavailable or not configured; used deterministic JS/TS fallback extractor.",),
    )


def _build_go_fallback_view(
    *,
    rel_path: str,
    content: str,
    source_sha256: str,
    extractor_fingerprint: str,
    token_counter,
) -> StructuralFileView:
    lines = content.splitlines()
    skeleton: list[str] = []
    imports: list[str] = []
    exports: list[str] = []
    symbols: list[StructuralSymbol] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("package "):
            skeleton.append(stripped)
            index += 1
            continue
        if stripped.startswith("import "):
            statement, index = _collect_paren_or_brace_statement(lines, index)
            imports.append(statement)
            skeleton.append(statement)
            index += 1
            continue
        if re.match(r"^(type|const|var)\s+\(", stripped):
            docs = _pending_doc(lines, index, "//")
            skeleton.extend(docs)
            statement, index = _collect_paren_or_brace_statement(lines, index)
            skeleton.append(statement)
            index += 1
            continue
        type_match = re.match(r"^type\s+([A-Za-z_]\w*)\s+", stripped)
        if type_match:
            docs = _pending_doc(lines, index, "//")
            skeleton.extend(docs)
            statement, end = _collect_statement(lines, index)
            name = type_match.group(1)
            if name[:1].isupper():
                exports.append(name)
            signature = _clip(statement)
            skeleton.append(signature)
            _add_symbol(symbols, name=name, kind="type", rel_path=rel_path, language="go", line=index + 1, signature=signature)
            index = end + 1
            continue
        fn_match = re.match(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", stripped)
        if fn_match:
            docs = _pending_doc(lines, index, "//")
            skeleton.extend(docs)
            statement, end = _collect_statement(lines, index)
            name = fn_match.group(1)
            if name[:1].isupper():
                exports.append(name)
            signature = _signature_with_body_marker(statement, "go")
            skeleton.append(signature)
            _add_symbol(symbols, name=name, kind="function", rel_path=rel_path, language="go", line=index + 1, signature=signature)
            index = end + 1
            continue
        const_match = re.match(r"^(const|var)\s+([A-Z]\w*)\b", stripped)
        if const_match and len(stripped) <= _MAX_SIGNATURE_LEN:
            name = const_match.group(2)
            exports.append(name)
            skeleton.append(stripped)
            _add_symbol(symbols, name=name, kind=const_match.group(1), rel_path=rel_path, language="go", line=index + 1, signature=stripped)
        index += 1

    return _source_metrics(
        rel_path=rel_path,
        language="go",
        skeleton_lines=skeleton,
        imports=imports,
        exports=exports,
        symbols=symbols,
        content=content,
        source_sha256=source_sha256,
        extractor_fingerprint=extractor_fingerprint,
        token_counter=token_counter,
        warnings=("Tree-sitter parser unavailable or not configured; used deterministic Go fallback extractor.",),
    )
