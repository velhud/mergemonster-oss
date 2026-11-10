from __future__ import annotations

from typing import Protocol


class FileProcessorLike(Protocol):
    _encoding: object | None

    def count_tokens(self, text: str) -> int:
        ...

    def read_file_content(self, file_path: str) -> str | None:
        ...

    def is_binary_file(self, file_path: str, chunk_size: int = 2048) -> bool:
        ...

    def processed_token_count(self, *args: object, **kwargs: object) -> int:
        ...

    def render_skeleton_block(self, *args: object, **kwargs: object) -> str:
        ...

    def skeleton_token_count(self, *args: object, **kwargs: object) -> int:
        ...

    def merge_group_token_details(self, *args: object, **kwargs: object) -> tuple[int, int]:
        ...
