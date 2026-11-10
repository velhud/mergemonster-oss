from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from .cache import StructureCache
from .language_detect import detect_language
from .models import StructuralFileView
from .python_ast_adapter import build_python_ast_view
from .tree_sitter_adapter import build_tree_sitter_view


EXTRACTOR_FINGERPRINT = "codemerge-structure-v1"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _root_hash(root: str | None) -> str:
    raw = os.path.abspath(root or "")
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _rel_path(path: str | Path, root: str | None) -> str:
    if root:
        try:
            return os.path.relpath(str(path), root)
        except Exception:
            pass
    return str(path)


def _default_token_counter(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def extract_file_structure(
    file_path: str | Path,
    *,
    root: str | None = None,
    token_counter: Callable[[str], int] | None = None,
    cache: StructureCache | None = None,
) -> StructuralFileView:
    path = Path(file_path)
    rel_path = _rel_path(path, root)
    counter = token_counter or _default_token_counter
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return StructuralFileView(
            rel_path=rel_path,
            language="unknown",
            parser_status="error",
            imports=(),
            exports=(),
            symbols=(),
            skeleton_text="",
            token_count_source=None,
            token_count_skeleton=0,
            compression_ratio=None,
            source_sha256="",
            extractor_fingerprint=EXTRACTOR_FINGERPRINT,
            warnings=(f"Could not read file: {exc}",),
        )

    source_sha = _sha256_text(content)
    cache_obj = cache or StructureCache()
    root_key = _root_hash(root)
    cached = cache_obj.get(
        root_hash=root_key,
        rel_path=rel_path,
        source_sha256=source_sha,
        extractor_fingerprint=EXTRACTOR_FINGERPRINT,
    )
    if cached is not None:
        return cached

    language, _confidence = detect_language(path, content)
    if language == "python":
        view = build_python_ast_view(
            path=path,
            rel_path=rel_path,
            content=content,
            source_sha256=source_sha,
            extractor_fingerprint=EXTRACTOR_FINGERPRINT,
            token_counter=counter,
        )
    else:
        view = build_tree_sitter_view(
            path=path,
            rel_path=rel_path,
            content=content,
            language=language,
            source_sha256=source_sha,
            extractor_fingerprint=EXTRACTOR_FINGERPRINT,
            token_counter=counter,
        ) or StructuralFileView(
            rel_path=rel_path,
            language=language,
            parser_status="unsupported",
            imports=(),
            exports=(),
            symbols=(),
            skeleton_text="",
            token_count_source=counter(content),
            token_count_skeleton=0,
            compression_ratio=None,
            source_sha256=source_sha,
            extractor_fingerprint=EXTRACTOR_FINGERPRINT,
            warnings=(f"No skeleton extractor is available for language '{language}'.",),
        )
    cache_obj.put(root_hash=root_key, view=view)
    return view
