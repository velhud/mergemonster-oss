from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Optional, Sequence

from codemerge.filemerger.types import FileProcessorLike as FileProcessor

from ._tokens import safe_count_tokens


def _safe_count_tokens(fp: FileProcessor, text: str) -> int:
    return safe_count_tokens(
        text,
        token_counter=fp.count_tokens,
        fallback_scale=1,
        use_tiktoken=False,
    )


def _split_text_by_token_budget(fp: FileProcessor, text: str, max_tokens: int) -> list[str]:
    if not text:
        return [""]
    max_tokens = max(1, int(max_tokens))
    enc = getattr(fp, "_encoding", None)
    if enc is not None:
        try:
            tokens = enc.encode(text, disallowed_special=())
        except Exception:
            tokens = None
        if tokens is not None:
            return [
                enc.decode(tokens[i : i + max_tokens])
                for i in range(0, len(tokens), max_tokens)
            ]

    chunks: list[str] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        low = start + 1
        high = text_len
        best = None
        while low <= high:
            mid = (low + high) // 2
            segment = text[start:mid]
            if _safe_count_tokens(fp, segment) <= max_tokens:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best is None:
            best = min(start + 1, text_len)
        chunks.append(text[start:best])
        start = best
    return chunks


def _line_chunk_text(fp: FileProcessor, text: str, max_tokens: int) -> list[str]:
    if text == "":
        return [""]
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    for line in lines:
        candidate = "".join(current) + line
        if _safe_count_tokens(fp, candidate) <= max_tokens:
            current.append(line)
            continue
        if current:
            chunks.append("".join(current))
            current = []
        if _safe_count_tokens(fp, line) <= max_tokens:
            current.append(line)
            continue
        for piece in _split_text_by_token_budget(fp, line, max_tokens):
            chunks.append(piece)
    if current:
        chunks.append("".join(current))
    return chunks


def _blocks_from_spans(total_lines: int, spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    cursor = 0
    for start, end in spans:
        start = max(0, min(start, total_lines - 1))
        end = max(0, min(end, total_lines - 1))
        if start > cursor:
            blocks.append((cursor, start - 1))
        blocks.append((start, max(start, end)))
        cursor = max(end + 1, cursor)
    if cursor <= total_lines - 1:
        blocks.append((cursor, total_lines - 1))
    return blocks


def _group_blocks_by_tokens(
    fp: FileProcessor,
    lines: list[str],
    blocks: Sequence[tuple[int, int]],
    max_tokens: int,
) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for start, end in blocks:
        block_text = "".join(lines[start : end + 1])
        if not block_text:
            continue
        candidate = "".join(current) + block_text
        if _safe_count_tokens(fp, candidate) <= max_tokens:
            current.append(block_text)
            continue
        if current:
            chunks.append("".join(current))
            current = []
        if _safe_count_tokens(fp, block_text) <= max_tokens:
            current.append(block_text)
            continue
        for piece in _line_chunk_text(fp, block_text, max_tokens):
            chunks.append(piece)
    if current:
        chunks.append("".join(current))
    return chunks


def _json_top_level_key_lines(text: str) -> list[int]:
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    lines = text.splitlines(keepends=True)
    boundaries: list[int] = []
    depth = 0
    in_str = False
    escape = False
    for idx, line in enumerate(lines):
        for ch in line:
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth = max(depth - 1, 0)
        if depth == 1 and line.lstrip().startswith('"'):
            boundaries.append(idx)
    return boundaries


def _yaml_top_level_key_lines(text: str) -> list[int]:
    lines = text.splitlines(keepends=True)
    boundaries: list[int] = []
    key_re = re.compile(r"^[A-Za-z0-9_\\-\"']+\\s*:")
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            continue
        if key_re.match(line):
            boundaries.append(idx)
    return boundaries


def _structure_aware_chunks(
    fp: FileProcessor,
    text: str,
    *,
    max_tokens: int,
    file_path: str | None,
) -> Optional[list[str]]:
    if not file_path or text == "":
        return None
    ext = Path(file_path).suffix.lower()
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines <= 1:
        return None

    spans: list[tuple[int, int]] = []
    if ext == ".py":
        try:
            tree = ast.parse(text)
        except Exception:
            tree = None
        if tree:
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = getattr(node, "lineno", None)
                    end = getattr(node, "end_lineno", None)
                    if start is None or end is None:
                        continue
                    spans.append((start - 1, end - 1))
    elif ext in {".js", ".ts", ".tsx", ".jsx"}:
        boundary_re = re.compile(
            r"^\s*(export\s+)?(default\s+)?(class|function)\b|"
            r"^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s+)?(function|\()"
        )
        for idx, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(("//", "/*", "*")):
                continue
            if boundary_re.search(line):
                spans.append((idx, idx))
    elif ext == ".json":
        boundaries = _json_top_level_key_lines(text)
        spans = [(idx, idx) for idx in boundaries]
    elif ext in {".yml", ".yaml"}:
        boundaries = _yaml_top_level_key_lines(text)
        spans = [(idx, idx) for idx in boundaries]

    if not spans:
        return None
    spans = sorted(set(spans))
    blocks = _blocks_from_spans(total_lines, spans)
    chunks = _group_blocks_by_tokens(fp, lines, blocks, max_tokens)
    if chunks and "".join(chunks) == text:
        return chunks
    return None


def _chunk_text(
    fp: FileProcessor,
    text: str,
    *,
    chunk_tokens: int,
    file_path: str | None = None,
) -> tuple[list[str], dict]:
    if text is None:
        return [""], {"coverage_ok": True, "coverage_mode": "empty", "chunk_lengths": [0]}
    max_tokens = max(1, chunk_tokens)
    enc = getattr(fp, "_encoding", None)
    if enc is not None:
        try:
            tokens = enc.encode(text, disallowed_special=())
        except Exception:
            tokens = None
        if tokens is not None and len(tokens) <= max_tokens:
            return [text], {
                "coverage_ok": True,
                "coverage_mode": "token",
                "chunk_lengths": [len(text)],
            }

    chunks = _structure_aware_chunks(fp, text, max_tokens=max_tokens, file_path=file_path)
    if chunks:
        return chunks, {
            "coverage_ok": True,
            "coverage_mode": "smart",
            "chunk_lengths": [len(c) for c in chunks],
        }

    chunks = _line_chunk_text(fp, text, max_tokens)
    coverage_ok = "".join(chunks) == text
    return chunks, {
        "coverage_ok": coverage_ok,
        "coverage_mode": "line",
        "chunk_lengths": [len(c) for c in chunks],
    }


def _format_chunk_content(file_path: str, chunk: str, chunk_idx: int, total_chunks: int) -> str:
    start_label_for_ai = f"======= START OF FILE: {file_path} ======="
    end_label_for_ai = f"======= END OF FILE: {file_path} ======="
    if total_chunks > 1:
        chunk_header = f"CHUNK {chunk_idx + 1}/{total_chunks}\n"
    else:
        chunk_header = ""
    return f"{start_label_for_ai}\n{chunk_header}{chunk}\n{end_label_for_ai}"
