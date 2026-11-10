from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ParserStatus = Literal["ok", "fallback", "unsupported", "error"]


@dataclass(frozen=True)
class StructuralSymbol:
    name: str
    kind: str
    file_rel_path: str
    language: str
    start_line: int
    end_line: int
    signature: str | None = None
    doc: str | None = None
    exported: bool = False
    parent_name: str | None = None


@dataclass(frozen=True)
class StructuralFileView:
    rel_path: str
    language: str
    parser_status: ParserStatus
    imports: tuple[str, ...]
    exports: tuple[str, ...]
    symbols: tuple[StructuralSymbol, ...]
    skeleton_text: str
    token_count_source: int | None
    token_count_skeleton: int | None
    compression_ratio: float | None
    source_sha256: str
    extractor_fingerprint: str
    warnings: tuple[str, ...] = ()

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)
