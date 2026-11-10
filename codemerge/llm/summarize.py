"""LLM-backed summarization helpers."""

from __future__ import annotations

from ._limits import (
    SUMMARY_MODEL,
    get_summary_model_caps,
    resolve_full_summary_model,
    resolve_max_output_tokens,
    resolve_summary_model,
)
from ._summarize_prompts import (
    COMPARE_PROMPT,
    PLAIN_FOLDER_PROMPT_TEMPLATE,
    PLAIN_REPO_PROMPT_TEMPLATE,
    PLAIN_SUMMARY_VERSION,
    SYSTEM_PROMPT,
    aggregate_prompt_fingerprint,
    build_plain_summary_prompt,
    build_file_summary_prompt,
    build_file_chunk_prompt,
    build_file_merge_prompt,
    build_group_summary_prompt,
    build_folder_summary_prompt,
    build_repo_summary_prompt,
    build_quick_selector_prompt,
    file_prompt_fingerprint,
    file_prompt_fingerprint_payload,
    plain_prompt_fingerprint,
)
from ._summarize_service import (
    compare_files_ai,
    fetch_ai_object_summary,
    fetch_ai_summaries,
    fetch_ai_text_summary,
)

__all__ = [
    "SUMMARY_MODEL",
    "COMPARE_PROMPT",
    "PLAIN_FOLDER_PROMPT_TEMPLATE",
    "PLAIN_REPO_PROMPT_TEMPLATE",
    "PLAIN_SUMMARY_VERSION",
    "SYSTEM_PROMPT",
    "aggregate_prompt_fingerprint",
    "build_plain_summary_prompt",
    "build_file_summary_prompt",
    "build_file_chunk_prompt",
    "build_file_merge_prompt",
    "build_group_summary_prompt",
    "build_folder_summary_prompt",
    "build_repo_summary_prompt",
    "build_quick_selector_prompt",
    "file_prompt_fingerprint",
    "file_prompt_fingerprint_payload",
    "plain_prompt_fingerprint",
    "resolve_summary_model",
    "resolve_full_summary_model",
    "resolve_max_output_tokens",
    "get_summary_model_caps",
    "fetch_ai_text_summary",
    "fetch_ai_object_summary",
    "fetch_ai_summaries",
    "compare_files_ai",
]
