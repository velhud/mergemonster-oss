from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..paths import state_directory

from .models import StructuralFileView, StructuralSymbol



DEFAULT_CACHE_PATH = state_directory() / "codemerge_structure_cache.db"


class StructureCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (state_directory() / "codemerge_structure_cache.db")

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS structural_file_cache (
              root_hash TEXT NOT NULL,
              rel_path TEXT NOT NULL,
              source_sha256 TEXT NOT NULL,
              extractor_fingerprint TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              updated_at REAL NOT NULL,
              PRIMARY KEY (root_hash, rel_path, source_sha256, extractor_fingerprint)
            )
            """
        )
        return conn

    def get(
        self,
        *,
        root_hash: str,
        rel_path: str,
        source_sha256: str,
        extractor_fingerprint: str,
    ) -> StructuralFileView | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT payload_json FROM structural_file_cache
                    WHERE root_hash=? AND rel_path=? AND source_sha256=? AND extractor_fingerprint=?
                    """,
                    (root_hash, rel_path, source_sha256, extractor_fingerprint),
                ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        try:
            return _view_from_json(json.loads(row[0]))
        except Exception:
            return None

    def put(self, *, root_hash: str, view: StructuralFileView) -> None:
        payload = json.dumps(_view_to_json(view), sort_keys=True)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO structural_file_cache
                    (root_hash, rel_path, source_sha256, extractor_fingerprint, payload_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        root_hash,
                        view.rel_path,
                        view.source_sha256,
                        view.extractor_fingerprint,
                        payload,
                        time.time(),
                    ),
                )
        except Exception:
            return


def _view_to_json(view: StructuralFileView) -> dict[str, object]:
    return {
        "rel_path": view.rel_path,
        "language": view.language,
        "parser_status": view.parser_status,
        "imports": list(view.imports),
        "exports": list(view.exports),
        "symbols": [sym.__dict__ for sym in view.symbols],
        "skeleton_text": view.skeleton_text,
        "token_count_source": view.token_count_source,
        "token_count_skeleton": view.token_count_skeleton,
        "compression_ratio": view.compression_ratio,
        "source_sha256": view.source_sha256,
        "extractor_fingerprint": view.extractor_fingerprint,
        "warnings": list(view.warnings),
    }


def _view_from_json(data: dict[str, object]) -> StructuralFileView:
    symbols = tuple(StructuralSymbol(**raw) for raw in data.get("symbols", []) if isinstance(raw, dict))
    return StructuralFileView(
        rel_path=str(data.get("rel_path") or ""),
        language=str(data.get("language") or "unknown"),
        parser_status=str(data.get("parser_status") or "unsupported"),  # type: ignore[arg-type]
        imports=tuple(str(x) for x in data.get("imports", []) if isinstance(x, str)),
        exports=tuple(str(x) for x in data.get("exports", []) if isinstance(x, str)),
        symbols=symbols,
        skeleton_text=str(data.get("skeleton_text") or ""),
        token_count_source=data.get("token_count_source") if isinstance(data.get("token_count_source"), int) else None,
        token_count_skeleton=data.get("token_count_skeleton") if isinstance(data.get("token_count_skeleton"), int) else None,
        compression_ratio=data.get("compression_ratio") if isinstance(data.get("compression_ratio"), float) else None,
        source_sha256=str(data.get("source_sha256") or ""),
        extractor_fingerprint=str(data.get("extractor_fingerprint") or ""),
        warnings=tuple(str(x) for x in data.get("warnings", []) if isinstance(x, str)),
    )
