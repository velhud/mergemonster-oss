"""Core helpers shared by FileProcessor, UI, and summariser."""

from .classify import ASSET_EXT, BINARY_EXT, CONFIG_EXT, DOC_EXT, FRONTEND_EXT, TEXT_EXT, categorise
from .io import is_binary_file, read_file_content, read_file_content_ex
from .token_cache import CACHE_BATCH_SIZE, DEFAULT_CACHE_PATH, TokenCache
from .normalize import NormalizeItem, NormalizeResult, sanitize_pasted_paths
from .tokens import (
    ENCODING_NAME,
    ENCODING_TAG,
    ENCODING_TAG_SUFFIX,
    count_tokens,
    count_tokens_iter,
    get_encoding,
    get_encoding_tag,
)

__all__ = [
    "ASSET_EXT",
    "BINARY_EXT",
    "CACHE_BATCH_SIZE",
    "CONFIG_EXT",
    "DEFAULT_CACHE_PATH",
    "DOC_EXT",
    "ENCODING_NAME",
    "ENCODING_TAG",
    "ENCODING_TAG_SUFFIX",
    "FRONTEND_EXT",
    "TEXT_EXT",
    "TokenCache",
    "categorise",
    "count_tokens",
    "count_tokens_iter",
    "get_encoding",
    "get_encoding_tag",
    "NormalizeItem",
    "NormalizeResult",
    "sanitize_pasted_paths",
    "is_binary_file",
    "read_file_content",
    "read_file_content_ex",
]
