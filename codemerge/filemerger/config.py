from __future__ import annotations
import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict

from ..llm.embed_types import (
    EmbedProvider,
    DEFAULT_GROQ_EMBED_MODEL,
    DEFAULT_OPENAI_EMBED_MODEL,
)
from ..llm.config import (
    DEFAULT_GROQ_SUMMARY_MODEL,
    DEFAULT_GROQ_DECIDER_MODEL,
    DEFAULT_GROQ_VERIFIER_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_OPENAI_SUMMARY_MODEL,
    DEFAULT_OPENAI_VERIFIER_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS,
    DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS,
    DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS,
    DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS,
    DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS,
    default_file_summary_chunk_tokens,
    default_aggregate_prompt_budget,
    default_summary_token_limit,
    enforce_output_token_minimums,
    normalize_groq_models,
    normalize_openai_embeddings,
)

from ..models import DEFAULT_TOKEN_MAX, DEFAULT_SIZE_MAX
from ..paths import state_directory
from .output import atomic_text_output


_DEFAULT_CONFIG_FILE = "settings.json"
_CONFIG_FILE_LOCK = threading.Lock()


def _codemerge_home() -> Path:
    return state_directory()


def _default_config_path() -> str:
    raw = str(_codemerge_home() / _DEFAULT_CONFIG_FILE)
    return os.path.abspath(os.path.expanduser(os.path.expandvars(raw)))


def _prepare_default_config() -> str:
    """Read existing settings once when upgrading an installation."""
    target = Path(_default_config_path())
    if target.exists():
        return str(target)
    for name in ("pythonmerge_config.json", "codemerge_config.json"):
        source = target.with_name(name)
        if not source.is_file():
            continue
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            with atomic_text_output(target, overwrite=False) as handle:
                json.dump(data, handle, indent=2)
        except FileExistsError:
            break
        except (OSError, ValueError) as exc:
            logging.warning("Could not import existing settings: %s", exc)
        else:
            break
    return str(target)


def _default_summary_cache_path() -> str:
    return str(_codemerge_home() / "codemerge_summary_cache.db")

# Prune UI/engine/ranking/neighbor defaults (locked single-hammer defaults)
# - prune_engine: "local" | "agent"
# - prune_ranking_mode: "keywords" | "embeddings" | "hybrid" | "hybrid_rerank"
# - prune_neighbors_mode: "none" | "same_file" | "same_package" | "auto"
# - prune_mode: "compact" | "safe"


# New pruning/LLM flags (defaults)
# - prune_goal_llm_enabled: use nano to interpret goal into seeds/exclusions
# - prune_rerank_llm_enabled: optional nano rerank of top files (off by default)
# - prune_neighbor_max: cap neighbor expansion
# - prune_exclude_tests: skip tests by default
# - groq_strict_mode: enforce strict JSON schema and no parallel tool calls

class AppConfig:
    """
    Configuration manager to load/save persistent user preferences.
    Stores data in a JSON file under the MergeMonster home directory by default.
    """

    def __init__(self, config_file: str | None = None) -> None:
        if not config_file and os.environ.get("PYTEST_CURRENT_TEST"):
            temp_name = f"codemerge_pytest_{uuid.uuid4().hex}.json"
            config_file = os.path.join(tempfile.gettempdir(), temp_name)
        elif not config_file:
            config_file = _prepare_default_config()
        config_file = os.path.expanduser(os.path.expandvars(config_file))
        config_file = os.path.abspath(config_file)
        self.config_file = config_file
        self.config = self._load_default_config()
        self._load_config()

    def _load_default_config(self) -> Dict:
        """Return default configuration as a dictionary."""
        cfg = {
            "config_version": 35,
                "last_directory": "",
                "last_output": "",
                "groq_model": DEFAULT_GROQ_MODEL,
                "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
                "groq_decider_model": DEFAULT_GROQ_DECIDER_MODEL,
                "groq_verifier_model": DEFAULT_GROQ_VERIFIER_MODEL,
                "groq_embedding_model": DEFAULT_GROQ_EMBED_MODEL,
                "openai_embedding_model": DEFAULT_OPENAI_EMBED_MODEL,
                "openai_embedding_dimensions": 0,
                "groq_max_workers": 0,
                "groq_use_batch": False,
                "batch_progress": False,
                "follow_symlinks": False,
                "exclude_ios_noise": True,
                "always_include_docs": True,
                "name_filter_glob": False,
                "show_totals_columns": False,
            "exclude_node_modules": True,
            "exclude_python_noise": False,
            "exclude_java_gradle_noise": False,
            "exclude_web_build_noise": False,
            "show_bytes_footer": True,
            "user_presets": {},
            "always_include_docs_list": [
                "readme",
                "license",
                "changelog",
                "contributing",
                "code_of_conduct",
                "security",
                "notice",
                "authors",
                "maintainers",
                "todo",
            ],
            # Git-aware scanning and change filters
            "use_git_files": False,
            "git_include_untracked": False,
            "git_since_ref": "",
            "git_only_changed": False,
            # Content search filter
            "content_search": "",
            "content_regex": False,
            "content_case_sensitive": False,
            "content_max_kb": 256,
            "duplicate_hash_max_kb": 256,
            # Output markdown enhancements
            "output_markdown_fences": False,
            "output_markdown_toc": False,
            # Footer display option: show only final tokens
            "footer_final_only": False,
            "content_hide_duplicates": False,
            "content_mark_duplicates": True,
            # Vendored files and extra git range
            "exclude_vendored": False,
            "git_until_ref": "HEAD",
            # Quick date helper
            "date_last_n_days": 0,
            # Custom extension → category overrides
            "custom_ext_map": {},
            # Parallel token counting (0 = auto by CPU)
            "token_workers": 0,
            # UI preferences
            "ui_font_size": 10,
            "ui_high_contrast": False,
            # Watch mode
            "watch_enabled": False,
            "watch_interval_sec": 3,
            "watch_sample_limit": 3000,
            # Summaries and per-file planning
            # Global default for files without explicit mode
            "summary_default_mode": "CODE",               # CODE | SKELETON | API_SURFACE | SIGNATURES | CODE_PLUS_SKELETON | CODE_PLUS_SHORT | CODE_PLUS_LONG | SUMMARY_* | *_PLUS_SKELETON
            # Staleness detection strategy (MVP)
            "summary_change_strategy": "mtime_size",      # mtime_size | sha256 | git
            # Where to persist summary cache (expanded on use)
            "summary_cache_db_path": _default_summary_cache_path(),
            # Auto behavior
            "auto_summarize_missing": True,
            "auto_refresh_summaries": True,
            # Bump to invalidate cache when prompt/schema changes
            "summary_prompt_version": 7,
            "summary_profile": "merge",
            "file_summary_max_output_tokens": DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS,
            "file_summary_unifier_max_output_tokens": DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS,
            "aggregate_summary_max_output_tokens": DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS,
            "aggregate_selector_max_output_tokens": DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS,
            "aggregate_critic_max_output_tokens": DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS,
            "summary_anchor_max_bytes": 64 * 1024,
            "aggregate_group_prompt_budget_ratio": 0.6,
            "aggregate_group_prompt_budget_tokens": 0,
            "groq_request_timeout_seconds": 120.0,
            "groq_summary_retry_budget": 2,
            # Skip vendored files by default
            "summarize_vendored": False,
            # On summary failure, fall back to code in output
            "fallback_to_code_on_summary_error": False,
            # Walk depth limit (0 = no limit)
            "max_walk_depth": 0,
                "file_types": {
                # Note: UI selection strictly defines the universe; these are only the scan defaults.
                # Common
                "py": True,
                "js": True,
                "ts": True,
                "tsx": True,
                "jsx": True,
                "vue": True,
                "html": False,
                "htm": False,
                "css": False,
                "scss": False,
                "sass": False,
                "json": False,
                "md": True,
                "txt": True,
                "docker": False,
                "yml": False,
                "conf": False,
                "xml": False,
                "sh": False,
                "": False,
                "dev": False,
                # iOS / Apple project related
                "swift": True,
                "m": True,
                "mm": True,
                "h": True,
                "hh": True,
                "hpp": True,
                "cxx": False,
                "mpp": False,
                "metal": True,
                "modulemap": True,
                "xcconfig": True,
                "plist": True,
                "pbxproj": True,
                "strings": True,
                "entitlements": True,
                "storyboard": True,
                "xib": True,
            },
            "excluded_dirs": [
                ".git",
                ".venv",
                "venv",
                "__pycache__",
                "addons",
                "fonts",
                "assets",
            ],
            "excluded_files": [".DS_Store"],
            "custom_excluded": [],
            "use_folder_name": False,
            "append_datetime": False,
            # Sorting and filtering
            "sort_by": "name",  # 'name' | 'size' | 'date'
            "sort_descending": False,
            "ignore_binary_files": False,
            "max_file_size_mb": 0.0,
            # advanced output options
            "include_file_tree": True,
            "include_json_manifest": False,
            "enumerate_lines": False,  # prefix merged lines with 1-based numbers
            "remove_empty": False,     # strip blank lines from merged output
            # Token filter semantics: apply token_min/max to transformed tokens
            # when remove_empty and/or enumerate_lines are enabled.
            "apply_token_filters_to_transforms": False,
            "custom_start_label": "======= START OF FILE: {file_display} =======",
            "custom_end_label": "======= END OF FILE: {file_display} =======",
            "custom_tree_start_label": "======= FILE TREE =======",
            "custom_tree_end_label": "======= END OF FILE TREE =======",
            # Output content vs structure
            "content_mode": "code_only",
            "structure_mode": "single_file",
            # Pruning/agent defaults
            "prune_goal_llm_enabled": True,
            "prune_rerank_llm_enabled": False,
            # NEW: LLM span selection path
            "prune_goal_llm_span_select": True,
            # LLM prefilter and neighbor cap for the span selector
            "llm_neighbors_mode": "same_file",   # "none" | "same_file" | "same_package"
            "llm_prefilter_top_k": 800,
            "llm_neighbor_max": 60,
            "prune_engine": "local",             # "local" | "agent"
            "prune_ranking_mode": "hybrid",      # "keywords" | "embeddings" | "hybrid" | "hybrid_rerank"
            "prune_neighbors_mode": "same_package", # "none" | "same_file" | "same_package" | "auto"
            # Locked defaults for single hammer
            "enable_embeddings": True,           # embeddings ON by default
            "retrieval_backend": "embeddings",
            "prune_mode": "compact",
            "prune_neighbor_max": 80,
            "prune_exclude_tests": True,
            "groq_strict_mode": True,
            # Noise handling
            "prune_downrank_noise": True,
            "prune_noise_dirs": ["tools", "scripts", "examples", "docs"],

            # >>> NEW: number of textual files for split-into-multiple mode
            "split_into_n": 5,
            # >>> NEW: token limit for token splitting
            "token_limit": 1_048_576,
            "summary_token_limit": default_summary_token_limit(DEFAULT_GROQ_SUMMARY_MODEL),
            "file_summary_chunk_tokens": default_file_summary_chunk_tokens(DEFAULT_GROQ_SUMMARY_MODEL),
            "aggregate_prompt_budget_tokens": default_aggregate_prompt_budget(DEFAULT_GROQ_SUMMARY_MODEL),
            # pruning options
            "embedding_provider": "groq",  # "groq" | "openai" | "none"
            "prune_token_budget": 45_000,
            "prune_keep_neighbors": True,
            "prune_top_k": 200,
            "prune_max_repairs": 3,
            "prune_span_to_file_threshold": 0,
            "prune_keep_class_for_method": True,
            "prune_core_budget_tokens": 0,
            "prune_repair_budget_tokens": 0,
            "prune_repair_max_spans_per_module": 50,
            "prune_repair_prefer_spans": True,
            "category_mask": {
                "BACKEND": True,
                "FRONTEND": True,
                "DOCS": True,
                "CONFIG": True,
                "ASSETS": True,
                "BINARY": True,
            },
            "last_preset": "",
            "token_filter_min": 0,
            "token_filter_max": DEFAULT_TOKEN_MAX,
            "size_filter_min_kb": 0.0,
            "size_filter_max_kb": DEFAULT_SIZE_MAX,
            "last_filters": {
                "categories": [
                    "BACKEND",
                    "FRONTEND",
                    "DOCS",
                    "CONFIG",
                    "ASSETS",
                ],
                "token_min": 0,
                "token_max": 10_000_000,
                "size_min": 0.0,
                "size_max": 1_000_000.0,
                "name_pattern": "",
                "date_after": 0,
                "date_before": 0,
                "date_after_manual": False,
                "date_before_manual": False,
            },
            "last_sort": {"column": "Path", "descending": False},
            "filter_panel_width": 260,
            "filters_collapsed": False,
            "view_mode": "hierarchy",   # "hierarchy" | "flat"
            "copy_to_clipboard": False,
            "ai_prompt_presets": [
                # persisted UI presets (untouched here)
                {"name": "Check for Bugs",       "template": "Review this codebase for bugs, vulnerabilities, and errors:\n\n{merged_content}"},
                {"name": "Suggest Improvements", "template": "Suggest optimizations, refactoring, and enhancements for this codebase:\n\n{merged_content}"},
                {"name": "Refactor Code",        "template": "Propose refactoring for better structure and maintainability:\n\n{merged_content}"},
                {"name": "Generate Docs",        "template": "Generate documentation and explanations for this codebase:\n\n{merged_content}"},
                {"name": "Analyze Architecture", "template": "Analyze the architecture, design patterns, and potential issues in this codebase:\n\n{merged_content}"},
                {"name": "Add Feature",          "template": "Update this codebase to add [feature description]:\n\n{merged_content}"},
                {"name": "Solve Problem",        "template": "Solve this specific problem in the codebase: [problem description]\n\n{merged_content}"},
            ],
            "selected_preset": "",
            # Optional persistence of user per-file picks (abs path -> FileMode)
            "per_file_modes": {},
            "per_folder_modes": {},
            "include_repo_summary_in_merge": False,
            "include_repo_summary_mode": "quick",
            "full_summary_file_kind": "long",
            "full_summary_model": "",
            "quick_summary_max_depth": 2,
            "quick_summary_file_kind": "short",
            "folder_summary_max_workers": 0,
            "repo_wrapper_threshold_one_folder": 0.90,
            "repo_wrapper_threshold_two_folder": 0.95,
            "repo_major_areas_max": 25,
            "strict_summary_required": False,
        }
        if cfg.get("embedding_provider") not in {"groq", "openai", "none"}:
            cfg["embedding_provider"] = "groq"
        if cfg.get("retrieval_backend") not in {"embeddings", "file_search"}:
            cfg["retrieval_backend"] = "embeddings"
        return cfg

    def _deep_merge(self, dict_base: Dict, dict_new: Dict) -> None:
        """Recursively merge ``dict_new`` into ``dict_base``."""
        for key, value in dict_new.items():
            if (
                isinstance(value, dict)
                and isinstance(dict_base.get(key), dict)
            ):
                self._deep_merge(dict_base[key], value)
            else:
                dict_base[key] = value

    def _load_config(self) -> None:
        """Load config from file if it exists; otherwise keep defaults."""
        backup_file = None
        try:
            if os.path.exists(self.config_file):
                backup_file = f"{self.config_file}.bak"
                try:
                    shutil.copy2(self.config_file, backup_file)
                except Exception as e:
                    logging.warning(f"Could not create config backup: {e}")
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved_config = json.load(f)

                    lf = saved_config.get("last_filters", {})
                    for key in ("date_after", "date_before"):
                        val = lf.get(key)
                        if isinstance(val, str):
                            if val:
                                parsed = None
                                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
                                    try:
                                        parsed = datetime.strptime(val, fmt).timestamp()
                                        break
                                    except Exception:
                                        continue
                                if parsed is not None:
                                    lf[key] = parsed
                                else:
                                    logging.warning("Invalid stored date '%s' for %s", val, key)
                                    # Keep the original string value if parsing fails
                                    lf[key] = val
                            else:
                                lf[key] = 0

                    # Check stored version and migrate if needed
                    version = saved_config.get("config_version", 1)
                    if version < 3:
                        saved_config["config_version"] = 3
                        version = 3
                    if version < 4:
                        default_mask = {
                            "BACKEND": True,
                            "FRONTEND": True,
                            "DOCS": True,
                            "CONFIG": True,
                            "ASSETS": True,
                            "BINARY": True,
                        }
                        saved_config.setdefault("category_mask", default_mask)
                        saved_config.setdefault("last_preset", "")
                        saved_config["config_version"] = 4
                        version = 4
                    if version < 5:
                        saved_config.setdefault("token_filter_min", 0)
                        saved_config.setdefault("token_filter_max", DEFAULT_TOKEN_MAX)
                        saved_config.setdefault("size_filter_min_kb", 0.0)
                        saved_config.setdefault("size_filter_max_kb", DEFAULT_SIZE_MAX)
                        saved_config["config_version"] = 5
                        version = 5
                    if version < 6:
                        saved_config.pop("use_legacy_ui", None)
                        saved_config.setdefault(
                            "last_filters",
                            {
                                "categories": [
                                    "BACKEND",
                                    "FRONTEND",
                                    "DOCS",
                                    "CONFIG",
                                    "ASSETS",
                                ],
                                "token_min": 0,
                                "token_max": 10_000_000,
                                "size_min": 0.0,
                                "size_max": 1_000_000.0,
                                "name_pattern": "",
                            },
                        )
                        saved_config.setdefault(
                            "last_sort", {"column": "Path", "descending": False}
                        )
                        saved_config["config_version"] = 6
                        version = 6
                    if version < 7:
                        lf = saved_config.setdefault("last_filters", {})
                        lf.setdefault("date_after", 0)
                        lf.setdefault("date_before", 0)
                        lf.setdefault("name_pattern", lf.get("name_pattern", ""))
                        saved_config.setdefault("last_sort", {"column": "Path", "descending": False})
                        saved_config["config_version"] = 7
                        version = 7
                    if version < 8:
                        saved_config["config_version"] = 8
                        version = 8
                    if version < 9:
                        saved_config.setdefault("filter_panel_width", 260)
                        saved_config.setdefault("filters_collapsed", False)
                        saved_config["config_version"] = 9
                        version = 9
                    if version < 10:
                        saved_config.setdefault("custom_excluded", [])
                        saved_config["config_version"] = 10
                        version = 10
                    if version < 11:
                        merge_mode = saved_config.pop("merge_mode", "code_only")
                        if merge_mode == "token_split":
                            saved_config["structure_mode"] = "split_tokens"
                            saved_config["content_mode"] = "code_only"
                        elif merge_mode == "split_into_multiple":
                            saved_config["structure_mode"] = "split_lines"
                            saved_config["content_mode"] = "code_only"
                        else:
                            saved_config["structure_mode"] = "single_file"
                            saved_config["content_mode"] = merge_mode
                        saved_config["config_version"] = 11
                        version = 11
                    if version < 12:
                        saved_config.setdefault(
                            "ai_prompt_presets",
                            [
                                {"name": "Check for Bugs", "template": "Review this codebase for bugs, vulnerabilities, and errors:\n\n{merged_content}"},
                                {"name": "Suggest Improvements", "template": "Suggest optimizations, refactoring, and enhancements for this codebase:\n\n{merged_content}"},
                                {"name": "Refactor Code", "template": "Propose refactoring for better structure and maintainability:\n\n{merged_content}"},
                                {"name": "Generate Docs", "template": "Generate documentation and explanations for this codebase:\n\n{merged_content}"},
                                {"name": "Analyze Architecture", "template": "Analyze the architecture, design patterns, and potential issues in this codebase:\n\n{merged_content}"},
                                {"name": "Add Feature", "template": "Update this codebase to add [feature description]:\n\n{merged_content}"},
                                {"name": "Solve Problem", "template": "Solve this specific problem in the codebase: [problem description]\n\n{merged_content}"},
                            ],
                        )
                        saved_config.setdefault("selected_preset", "")
                        saved_config["config_version"] = 12
                        version = 12
                    if version < 13:
                        saved_config.setdefault("openai_max_workers", 0)
                        saved_config["config_version"] = 13
                        version = 13
                    if version < 14:
                        limit_default = default_summary_token_limit(
                            DEFAULT_OPENAI_SUMMARY_MODEL
                        )
                        saved_config.setdefault("summary_token_limit", limit_default)
                        saved_config["config_version"] = 14
                        version = 14
                    if version < 15:
                        old_model = saved_config.get("openai_model", DEFAULT_OPENAI_MODEL)
                        saved_config.setdefault("openai_decider_model", old_model)
                        saved_config.setdefault("openai_verifier_model", DEFAULT_OPENAI_VERIFIER_MODEL)
                        saved_config["config_version"] = 15
                        version = 15
                    if version < 16:
                        saved_config.setdefault("openai_embedding_model", DEFAULT_OPENAI_EMBED_MODEL)
                        saved_config["config_version"] = 16
                        version = 16
                    if version < 17:
                        # Migrate embedding model default to the latest if user still has old defaults
                        emb = saved_config.get("openai_embedding_model")
                        if not emb or emb in {"text-embedding-3-large", "text-embedding-3-small"}:
                            saved_config["openai_embedding_model"] = DEFAULT_OPENAI_EMBED_MODEL
                        saved_config["config_version"] = 17
                        version = 17
                    if version < 18:
                        saved_config.setdefault("prune_span_to_file_threshold", 0)
                        saved_config.setdefault("prune_keep_class_for_method", True)
                        # ensure new LLM span-selection keys exist on older configs
                        saved_config.setdefault("prune_goal_llm_span_select", True)
                        saved_config.setdefault("llm_neighbors_mode", "same_file")
                        saved_config.setdefault("llm_prefilter_top_k", 800)
                        saved_config.setdefault("llm_neighbor_max", 60)
                        saved_config.setdefault("prune_core_budget_tokens", 0)
                        saved_config.setdefault("prune_repair_budget_tokens", 0)
                        saved_config.setdefault("prune_repair_max_spans_per_module", 50)
                        saved_config.setdefault("prune_repair_prefer_spans", True)
                        saved_config["config_version"] = 18
                        version = 18
                    if version < 19:
                        saved_config.setdefault("footer_final_only", False)
                        saved_config.setdefault("apply_token_filters_to_transforms", False)
                        saved_config["config_version"] = 19
                        version = 19
                    if version < 20:
                        remap = {
                            "openai_model": "groq_model",
                            "openai_decider_model": "groq_decider_model",
                            "openai_verifier_model": "groq_verifier_model",
                            "openai_embedding_model": "groq_embedding_model",
                            "openai_max_workers": "groq_max_workers",
                            "openai_use_batch": "groq_use_batch",
                            "openai_strict_mode": "groq_strict_mode",
                        }
                        for old, new in remap.items():
                            if old in saved_config and new not in saved_config:
                                saved_config[new] = saved_config.pop(old)
                            else:
                                saved_config.pop(old, None)
                        saved_config.setdefault("groq_model", DEFAULT_GROQ_MODEL)
                        saved_config.setdefault("groq_summary_model", DEFAULT_GROQ_SUMMARY_MODEL)
                        saved_config.setdefault("groq_decider_model", DEFAULT_GROQ_DECIDER_MODEL)
                        saved_config.setdefault("groq_verifier_model", DEFAULT_GROQ_VERIFIER_MODEL)
                        saved_config.setdefault("groq_embedding_model", DEFAULT_GROQ_EMBED_MODEL)
                        saved_config.setdefault("groq_max_workers", 0)
                        saved_config.setdefault("groq_use_batch", False)
                        saved_config.setdefault("groq_strict_mode", True)
                        saved_config["config_version"] = 20
                        version = 20
                    if version < 21:
                        # Migrate any legacy 'summary_default_kind' -> 'summary_default_mode'
                        legacy_kind = saved_config.pop("summary_default_kind", None)
                        if legacy_kind and "summary_default_mode" not in saved_config:
                            try:
                                lk = str(legacy_kind).lower()
                                saved_config["summary_default_mode"] = (
                                    "SUMMARY_SHORT" if lk.startswith("short") else "SUMMARY_LONG"
                                )
                            except Exception:
                                saved_config["summary_default_mode"] = "CODE"
                        saved_config.setdefault("summary_default_mode", "CODE")
                        saved_config.setdefault("summary_change_strategy", "mtime_size")
                        saved_config.setdefault(
                            "summary_cache_db_path",
                            _default_summary_cache_path(),
                        )
                        saved_config.setdefault("auto_summarize_missing", True)
                        saved_config.setdefault("auto_refresh_summaries", True)
                        saved_config.setdefault("summary_prompt_version", 1)
                        saved_config.setdefault("summarize_vendored", False)
                        saved_config.setdefault("fallback_to_code_on_summary_error", False)
                        saved_config.setdefault("per_file_modes", {})
                        saved_config["config_version"] = 21
                        version = 21
                    if version < 22:
                        saved_config.setdefault("openai_embedding_model", DEFAULT_OPENAI_EMBED_MODEL)
                        try:
                            dims_val = int(saved_config.get("openai_embedding_dimensions", 0) or 0)
                        except Exception:
                            dims_val = 0
                        if dims_val < 0:
                            dims_val = 0
                        saved_config["openai_embedding_dimensions"] = dims_val
                        if saved_config.get("embedding_provider") not in {"groq", "openai", "none"}:
                            saved_config["embedding_provider"] = "groq"
                        saved_config["config_version"] = 22
                        version = 22
                        file_types = saved_config.get("file_types")
                        if not isinstance(file_types, dict):
                            file_types = {}
                            saved_config["file_types"] = file_types

                if version < 23:
                    file_types = saved_config.get("file_types")
                    if not isinstance(file_types, dict):
                        file_types = {}
                        saved_config["file_types"] = file_types
                    fe_defaults = {
                        "ts": True,
                        "tsx": True,
                        "jsx": True,
                        "vue": True,
                        "scss": False,
                        "sass": False,
                        "htm": False,
                    }
                    for ext, default in fe_defaults.items():
                        file_types.setdefault(ext, default)
                    saved_config["config_version"] = 23
                    version = 23
                    # Inject defaults for new pruning flags if missing
                    saved_config.setdefault("prune_goal_llm_enabled", True)
                    saved_config.setdefault("prune_rerank_llm_enabled", False)
                    saved_config.setdefault("prune_goal_llm_span_select", True)
                    saved_config.setdefault("llm_neighbors_mode", "same_file")
                    saved_config.setdefault("llm_prefilter_top_k", 800)
                    saved_config.setdefault("llm_neighbor_max", 60)
                    # Force compact default for one-file output
                    if saved_config.get("prune_mode") not in {"safe", "compact"}:
                        saved_config["prune_mode"] = "compact"
                    # Ensure new controls defaults
                    saved_config.setdefault("prune_engine", "local")
                    saved_config.setdefault("prune_ranking_mode", "hybrid")
                    saved_config.setdefault("prune_neighbors_mode", "same_package")
                    saved_config.setdefault("enable_embeddings", True)
                    saved_config.setdefault("retrieval_backend", "embeddings")
                    # Bounded repairs default
                    if "prune_max_repairs" not in saved_config:
                        saved_config["prune_max_repairs"] = 3
                    saved_config.setdefault("prune_neighbor_max", 80)
                    saved_config.setdefault("prune_exclude_tests", True)
                    # Enforce strict Groq behavior by default
                    saved_config.setdefault("groq_strict_mode", True)
                    # Noise suppression defaults
                    saved_config.setdefault("prune_downrank_noise", True)

                if version < 24:
                    summary_version = saved_config.get("summary_prompt_version", 1)
                    try:
                        summary_version = int(summary_version or 0)
                    except Exception:
                        summary_version = 0
                    if summary_version < 2:
                        saved_config["summary_prompt_version"] = 2
                    saved_config["groq_summary_model"] = DEFAULT_GROQ_SUMMARY_MODEL
                    saved_config["config_version"] = 24
                    version = 24
                    if "prune_noise_dirs" not in saved_config:
                        saved_config["prune_noise_dirs"] = ["tools", "scripts", "examples", "docs"]

                    file_types = saved_config.get("file_types", {})
                    if "none" in file_types:
                        file_types[""] = file_types.pop("none")

                if version < 26:
                    summary_version = saved_config.get("summary_prompt_version", 1)
                    try:
                        summary_version = int(summary_version or 0)
                    except Exception:
                        summary_version = 0
                    if summary_version < 4:
                        saved_config["summary_prompt_version"] = 4
                    saved_config["config_version"] = 26
                    version = 26

                    # Ensure presence of new keys if old config was missing them.
                    # Defaults chosen to better support iOS/Xcode projects out of the box.
                    ensure_false = [
                        "docker",
                        "yml",
                        "conf",
                        "xml",
                        "sh",
                        "",
                        "dev",
                        "cxx",
                        "mpp",
                    ]
                    ensure_true = [
                        # iOS and Apple project ecosystem
                        "swift",
                        "m",
                        "mm",
                        "h",
                        "hh",
                        "hpp",
                        "metal",
                        "modulemap",
                        "xcconfig",
                        "plist",
                        "pbxproj",
                        "strings",
                        "entitlements",
                        "storyboard",
                        "xib",
                        # Common docs
                        "md",
                        "txt",
                        # Xcode workspace/scheme text files
                        "xcworkspacedata",
                        "xcscheme",
                        "xcuserstate",
                        "xcsettings",
                    ]
                    for ext in ensure_false:
                        file_types.setdefault(ext, False)
                    for ext in ensure_true:
                        file_types.setdefault(ext, True)

                    saved_config["file_types"] = file_types

                    if "sort_by" not in saved_config:
                        saved_config["sort_by"] = "name"
                    if "sort_descending" not in saved_config:
                        saved_config["sort_descending"] = False
                    if "ignore_binary_files" not in saved_config:
                        saved_config["ignore_binary_files"] = False
                    if "max_file_size_mb" not in saved_config:
                        saved_config["max_file_size_mb"] = 0.0

                    if "include_file_tree" not in saved_config:
                        saved_config["include_file_tree"] = True
                    if "include_json_manifest" not in saved_config:
                        saved_config["include_json_manifest"] = False
                    if "follow_symlinks" not in saved_config:
                        saved_config["follow_symlinks"] = False
                    if "exclude_ios_noise" not in saved_config:
                        saved_config["exclude_ios_noise"] = True
                    if "always_include_docs" not in saved_config:
                        saved_config["always_include_docs"] = True
                    if "name_filter_glob" not in saved_config:
                        saved_config["name_filter_glob"] = False
                    if "show_totals_columns" not in saved_config:
                        saved_config["show_totals_columns"] = False
                    if "exclude_node_modules" not in saved_config:
                        saved_config["exclude_node_modules"] = True
                    if "exclude_python_noise" not in saved_config:
                        saved_config["exclude_python_noise"] = False
                    if "exclude_java_gradle_noise" not in saved_config:
                        saved_config["exclude_java_gradle_noise"] = False
                    if "exclude_web_build_noise" not in saved_config:
                        saved_config["exclude_web_build_noise"] = False
                    if "show_bytes_footer" not in saved_config:
                        saved_config["show_bytes_footer"] = True
                    if "user_presets" not in saved_config:
                        saved_config["user_presets"] = {}
                    if "always_include_docs_list" not in saved_config:
                        saved_config["always_include_docs_list"] = self.config["always_include_docs_list"]
                    # New: git-aware scanning and content search
                    saved_config.setdefault("use_git_files", False)
                    saved_config.setdefault("git_include_untracked", False)
                    saved_config.setdefault("git_since_ref", "")
                    saved_config.setdefault("git_only_changed", False)
                    saved_config.setdefault("content_search", "")
                    saved_config.setdefault("content_regex", False)
                    saved_config.setdefault("content_case_sensitive", False)
                    saved_config.setdefault("content_max_kb", 256)
                    saved_config.setdefault("duplicate_hash_max_kb", 256)
                    saved_config.setdefault("output_markdown_fences", False)
                    saved_config.setdefault("output_markdown_toc", False)
                    saved_config.setdefault("exclude_vendored", False)
                    saved_config.setdefault("git_until_ref", "HEAD")
                    saved_config.setdefault("date_last_n_days", 0)
                    saved_config.setdefault("custom_ext_map", {})
                    saved_config.setdefault("token_workers", 0)
                    saved_config.setdefault("content_mark_duplicates", True)
                    saved_config.setdefault("ui_font_size", 10)
                    saved_config.setdefault("ui_high_contrast", False)
                    saved_config.setdefault("watch_enabled", False)
                    saved_config.setdefault("watch_interval_sec", 3)
                    saved_config.setdefault("watch_sample_limit", 3000)
                    saved_config.setdefault("max_walk_depth", 0)
                    saved_config.setdefault("content_hide_duplicates", False)
                    if "enumerate_lines" not in saved_config:
                        saved_config["enumerate_lines"] = False
                    if "remove_empty" not in saved_config:
                        saved_config["remove_empty"] = False
                    if "custom_start_label" not in saved_config:
                        saved_config["custom_start_label"] = (
                            "======= START OF FILE: {file_display} ======="
                        )
                    if "custom_end_label" not in saved_config:
                        saved_config["custom_end_label"] = (
                            "======= END OF FILE: {file_display} ======="
                        )
                    if "custom_tree_start_label" not in saved_config:
                        saved_config["custom_tree_start_label"] = (
                            "======= FILE TREE ======="
                        )
                    if "custom_tree_end_label" not in saved_config:
                        saved_config["custom_tree_end_label"] = (
                            "======= END OF FILE TREE ======="
                        )
                    if "content_mode" not in saved_config:
                        saved_config["content_mode"] = "code_only"
                    if "structure_mode" not in saved_config:
                        saved_config["structure_mode"] = "single_file"
                    if "split_into_n" not in saved_config:
                        saved_config["split_into_n"] = 5
                    if "token_limit" not in saved_config:
                        saved_config["token_limit"] = 1_048_576
                    if "summary_token_limit" not in saved_config:
                        saved_config[
                            "summary_token_limit"
                        ] = default_summary_token_limit(DEFAULT_GROQ_SUMMARY_MODEL)
                    if "file_summary_chunk_tokens" not in saved_config:
                        saved_config[
                            "file_summary_chunk_tokens"
                        ] = default_file_summary_chunk_tokens(DEFAULT_GROQ_SUMMARY_MODEL)
                    if "aggregate_prompt_budget_tokens" not in saved_config:
                        saved_config[
                            "aggregate_prompt_budget_tokens"
                        ] = default_aggregate_prompt_budget(DEFAULT_GROQ_SUMMARY_MODEL)
                    if "view_mode" not in saved_config:
                        saved_config["view_mode"] = "hierarchy"
                    if "filter_panel_width" not in saved_config:
                        saved_config["filter_panel_width"] = 260
                    if "filters_collapsed" not in saved_config:
                        saved_config["filters_collapsed"] = False
                    if "custom_excluded" not in saved_config:
                        saved_config["custom_excluded"] = []
                    if "groq_model" not in saved_config:
                        saved_config["groq_model"] = DEFAULT_GROQ_MODEL
                    if "groq_decider_model" not in saved_config:
                        saved_config["groq_decider_model"] = DEFAULT_GROQ_DECIDER_MODEL
                    if "groq_verifier_model" not in saved_config:
                        saved_config["groq_verifier_model"] = DEFAULT_GROQ_VERIFIER_MODEL
                    if "groq_max_workers" not in saved_config:
                        saved_config["groq_max_workers"] = 0
                    if "groq_use_batch" not in saved_config:
                        saved_config["groq_use_batch"] = False
                    if "batch_progress" not in saved_config:
                        saved_config["batch_progress"] = False
                    if "copy_to_clipboard" not in saved_config:
                        saved_config["copy_to_clipboard"] = False
                    if "ai_prompt_presets" not in saved_config:
                        saved_config["ai_prompt_presets"] = self.config.get("ai_prompt_presets", [])
                    if "selected_preset" not in saved_config:
                        saved_config["selected_preset"] = ""

                    lf = saved_config.get("last_filters", {})
                    if isinstance(lf.get("date_before"), (int, float)) and lf.get("date_before") >= 4102444800:
                        lf["date_before"] = 0
                    lf.setdefault("date_after_manual", False)
                    lf.setdefault("date_before_manual", False)

                    # Remove obsolete keys
                    obsolete = ["include_summaries"]
                    for key in obsolete:
                        saved_config.pop(key, None)
                        self.config.pop(key, None)

                if version < 27:
                    summary_version = saved_config.get("summary_prompt_version", 1)
                    try:
                        summary_version = int(summary_version or 0)
                    except Exception:
                        summary_version = 0
                    if summary_version < 5:
                        saved_config["summary_prompt_version"] = 5
                    saved_config["config_version"] = 27
                    version = 27

                if version < 28:
                    summary_version = saved_config.get("summary_prompt_version", 1)
                    try:
                        summary_version = int(summary_version or 0)
                    except Exception:
                        summary_version = 0
                    if summary_version < 6:
                        saved_config["summary_prompt_version"] = 6
                    saved_config.setdefault(
                        "file_summary_max_output_tokens",
                        DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS,
                    )
                    saved_config.setdefault(
                        "file_summary_unifier_max_output_tokens",
                        DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS,
                    )
                    saved_config.setdefault(
                        "aggregate_summary_max_output_tokens",
                        DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS,
                    )
                    saved_config.setdefault(
                        "aggregate_selector_max_output_tokens",
                        DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS,
                    )
                    saved_config.setdefault(
                        "aggregate_critic_max_output_tokens",
                        DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS,
                    )
                    saved_config["config_version"] = 28
                    version = 28

                if version < 29:
                    saved_config.setdefault("include_repo_summary_mode", "quick")
                    saved_config.setdefault("full_summary_file_kind", "long")
                    saved_config["config_version"] = 29
                    version = 29

                if version < 30:
                    saved_config.setdefault("auto_refresh_summaries", True)
                    saved_config["config_version"] = 30
                    version = 30

                if version < 32:
                    try:
                        current = int(saved_config.get("aggregate_summary_max_output_tokens", 0) or 0)
                    except Exception:
                        current = 0
                    if current <= 15_000:
                        saved_config["aggregate_summary_max_output_tokens"] = 25_000
                    saved_config["config_version"] = 32
                    version = 32

                if version < 33:
                    summary_version = saved_config.get("summary_prompt_version", 1)
                    try:
                        summary_version = int(summary_version or 0)
                    except Exception:
                        summary_version = 0
                    if summary_version < 7:
                        saved_config["summary_prompt_version"] = 7
                    saved_config.setdefault("summary_profile", "merge")
                    saved_config.setdefault("summary_anchor_max_bytes", 64 * 1024)
                    saved_config.setdefault("aggregate_group_prompt_budget_ratio", 0.6)
                    saved_config.setdefault("aggregate_group_prompt_budget_tokens", 0)
                    saved_config.setdefault("quick_summary_max_depth", 2)
                    saved_config.setdefault("quick_summary_file_kind", "short")
                    saved_config.setdefault("folder_summary_max_workers", 0)
                    saved_config["config_version"] = 33
                    version = 33

                if version < 34:
                    saved_config.setdefault("groq_request_timeout_seconds", 120.0)
                    saved_config["config_version"] = 34
                    version = 34

                if version < 35:
                    saved_config.setdefault("groq_summary_retry_budget", 2)
                    saved_config["config_version"] = 35
                    version = 35

                for key in (
                    "aggregate_run_critic",
                    "summary_strict_schema",
                    "summary_validation_enabled",
                    "full_batch_model",
                    "full_reduce_model",
                    "full_final_model",
                    "full_map_enabled",
                    "full_dependency_synth_enabled",
                    "full_densifier_enabled",
                    "full_renderer_enabled",
                    "full_map_batch_model",
                    "full_map_reduce_model",
                    "full_map_final_model",
                    "full_dependency_synth_model",
                    "full_densifier_model",
                    "full_key_file_allowlist_max",
                    "full_subarea_allowlist_max",
                    "full_entrypoints_allowlist_max",
                    "full_strict_mode",
                    "full_max_workers",
                    "full_child_folder_workers",
                    "full_batch_workers",
                    "full_reduce_workers",
                    "full_map_atom_workers",
                    "full_flow_merge_workers",
                    "full_file_summary_workers",
                    "full_retry_max_attempts",
                    "full_retry_backoff_base_sec",
                    "full_retry_backoff_cap_sec",
                ):
                    saved_config.pop(key, None)

                # Merge loaded config
                self._deep_merge(self.config, saved_config)
                if self.config.get("embedding_provider") not in {"groq", "openai", "none"}:
                    self.config["embedding_provider"] = "groq"
                if isinstance(self.config, dict):
                    enforce_output_token_minimums(self.config)
                self.save_config()
        except Exception as e:
            logging.error(
                f"Error loading config file '{self.config_file}': {e}"
            )
            if backup_file and os.path.exists(backup_file):
                try:
                    shutil.copy2(backup_file, self.config_file)
                    with open(backup_file, "r", encoding="utf-8") as bf:
                        backup_config = json.load(bf)
                    self.config = self._load_default_config()
                    self._deep_merge(self.config, backup_config)
                except Exception as restore_err:
                    logging.error(
                        f"Failed to restore configuration backup: {restore_err}"
                    )

        if isinstance(self.config, dict):
            normalize_groq_models(self.config, save_callback=self.save_config)
            normalize_openai_embeddings(self.config)

    def save_config(self) -> None:
        """Save current config to disk as JSON."""
        try:
            if isinstance(self.config, dict):
                normalize_openai_embeddings(self.config)
            data = dict(self.config)
            ft = data.get("file_types", {})
            ft.pop("none", None)
            data["file_types"] = ft
            # Ensure Groq-specific keys exist when persisting the configuration.
            allowed_openai_keys = {
                "openai_embedding_model",
                "openai_embedding_dimensions",
            }
            for legacy in list(data.keys()):
                if legacy.startswith("openai_") and legacy not in allowed_openai_keys:
                    data.pop(legacy, None)
            data.setdefault("groq_model", DEFAULT_GROQ_MODEL)
            data.setdefault("groq_summary_model", DEFAULT_GROQ_SUMMARY_MODEL)
            data.setdefault("groq_decider_model", DEFAULT_GROQ_DECIDER_MODEL)
            data.setdefault("groq_verifier_model", DEFAULT_GROQ_VERIFIER_MODEL)
            data.setdefault("groq_embedding_model", DEFAULT_GROQ_EMBED_MODEL)
            data.setdefault("groq_max_workers", 0)
            data.setdefault("groq_use_batch", False)
            data.setdefault("groq_strict_mode", True)
            data.setdefault("openai_embedding_model", DEFAULT_OPENAI_EMBED_MODEL)
            try:
                dims_val = int(data.get("openai_embedding_dimensions", 0) or 0)
            except Exception:
                dims_val = 0
            if dims_val < 0:
                dims_val = 0
            data["openai_embedding_dimensions"] = dims_val
            # Ensure new keys survive
            data.setdefault("prune_engine", "local")
            data.setdefault("prune_ranking_mode", "hybrid")
            data.setdefault("prune_neighbors_mode", "same_package")
            data.setdefault("enable_embeddings", True)
            data.setdefault("retrieval_backend", "embeddings")
            if "prune_mode" not in data:
                data["prune_mode"] = "compact"
            # Ensure summary planning keys persist
            data.setdefault("summary_default_mode", "CODE")
            data.setdefault("summary_change_strategy", "mtime_size")
            data.setdefault(
                "summary_cache_db_path",
                _default_summary_cache_path(),
            )
            data.setdefault("auto_summarize_missing", True)
            data.setdefault("auto_refresh_summaries", True)
            data.setdefault("summary_prompt_version", 7)
            data.setdefault("summary_profile", "merge")
            data.setdefault(
                "file_summary_max_output_tokens",
                DEFAULT_FILE_SUMMARY_MAX_OUTPUT_TOKENS,
            )
            data.setdefault(
                "file_summary_unifier_max_output_tokens",
                DEFAULT_FILE_SUMMARY_UNIFIER_MAX_OUTPUT_TOKENS,
            )
            data.setdefault(
                "aggregate_summary_max_output_tokens",
                DEFAULT_AGGREGATE_SUMMARY_MAX_OUTPUT_TOKENS,
            )
            data.setdefault(
                "aggregate_selector_max_output_tokens",
                DEFAULT_AGGREGATE_SELECTOR_MAX_OUTPUT_TOKENS,
            )
            data.setdefault(
                "aggregate_critic_max_output_tokens",
                DEFAULT_AGGREGATE_CRITIC_MAX_OUTPUT_TOKENS,
            )
            data.setdefault("summary_anchor_max_bytes", 64 * 1024)
            data.setdefault("aggregate_group_prompt_budget_ratio", 0.6)
            data.setdefault("aggregate_group_prompt_budget_tokens", 0)
            data.setdefault("groq_request_timeout_seconds", 120.0)
            data.setdefault("groq_summary_retry_budget", 2)
            data.setdefault("summarize_vendored", False)
            data.setdefault("fallback_to_code_on_summary_error", False)
            data.setdefault("per_file_modes", {})
            data.setdefault("per_folder_modes", {})
            data.setdefault("include_repo_summary_in_merge", False)
            data.setdefault("include_repo_summary_mode", "quick")
            data.setdefault("full_summary_file_kind", "long")
            data.setdefault("quick_summary_max_depth", 2)
            data.setdefault("quick_summary_file_kind", "short")
            data.setdefault("folder_summary_max_workers", 0)
            data.setdefault("repo_wrapper_threshold_one_folder", 0.90)
            data.setdefault("repo_wrapper_threshold_two_folder", 0.95)
            data.setdefault("repo_major_areas_max", 25)
            data.setdefault("strict_summary_required", False)
            floors = {
                "aggregate_selector_max_output_tokens": 15_000,
                "aggregate_summary_max_output_tokens": 25_000,
                "aggregate_critic_max_output_tokens": 15_000,
                "file_summary_max_output_tokens": 15_000,
                "file_summary_unifier_max_output_tokens": 15_000,
            }
            for key, floor in floors.items():
                try:
                    v = int(data.get(key, 0) or 0)
                except Exception:
                    v = 0
                if v < floor:
                    data[key] = floor
            config_dir = os.path.dirname(self.config_file) or "."
            os.makedirs(config_dir, exist_ok=True)
            temp_path: str | None = None
            with _CONFIG_FILE_LOCK:
                if os.path.exists(self.config_file):
                    try:
                        existing_data = json.loads(
                            Path(self.config_file).read_text(encoding="utf-8")
                        )
                    except Exception:
                        existing_data = None
                    if existing_data == data:
                        self.config = data
                        return
                try:
                    with tempfile.NamedTemporaryFile(
                        "w",
                        delete=False,
                        dir=config_dir,
                        encoding="utf-8",
                    ) as tmp:
                        json.dump(data, tmp, indent=2)
                        tmp.flush()
                        os.fsync(tmp.fileno())
                        temp_path = tmp.name

                    backup_file = f"{self.config_file}.bak"
                    if os.path.exists(self.config_file):
                        try:
                            shutil.copy2(self.config_file, backup_file)
                        except Exception as exc:
                            logging.warning(f"Could not create config backup: {exc}")

                    os.replace(temp_path, self.config_file)
                    temp_path = None
                finally:
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except Exception:
                            pass
        except Exception as e:
            logging.error(f"Error saving configuration: {str(e)}")

    def get_config(self) -> Dict:
        """Return the current configuration dictionary."""
        return self.config

    @staticmethod
    def _normalize_exclusion_list(values: object) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, set, tuple)):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in values:
            if isinstance(item, str):
                raw = item.strip()
            else:
                try:
                    raw = str(item).strip()
                except Exception:
                    continue
            if not raw:
                continue
            expanded = raw
            try:
                expanded = os.path.expandvars(os.path.expanduser(raw))
                if os.path.isabs(expanded):
                    norm = os.path.normcase(os.path.abspath(expanded))
                else:
                    norm = os.path.normcase(os.path.normpath(expanded))
            except Exception:
                norm = raw
            if norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
        return out

    def embedding_provider(self) -> EmbedProvider:
        """Return the configured :class:`EmbedProvider`.

        Any invalid value falls back to ``EmbedProvider.GROQ`` with remote
        embeddings disabled.
        """
        try:
            return EmbedProvider(self.config.get("embedding_provider", "groq"))
        except Exception:
            return EmbedProvider.GROQ

    def update_config(self, new_config: Dict) -> None:
        """Update current config dictionary and save to file."""
        patch = dict(new_config or {})
        if "excluded_dirs" in patch:
            patch["excluded_dirs"] = self._normalize_exclusion_list(patch.get("excluded_dirs"))
        if "excluded_files" in patch:
            patch["excluded_files"] = self._normalize_exclusion_list(patch.get("excluded_files"))
        if "custom_excluded" in patch:
            custom_excluded = patch.get("custom_excluded")
            if isinstance(custom_excluded, str):
                custom_excluded = [custom_excluded]
            if isinstance(custom_excluded, (list, set, tuple)):
                patch["custom_excluded"] = [
                    str(x)
                    for x in custom_excluded
                    if isinstance(x, str) and x.strip()
                ]
            else:
                patch["custom_excluded"] = []
        self._deep_merge(self.config, patch)
        self.save_config()

    def update_exclusions(
        self,
        *,
        excluded_dirs: object | None = None,
        excluded_files: object | None = None,
        custom_excluded: object | None = None,
    ) -> None:
        patch: Dict[str, object] = {}
        if excluded_dirs is not None:
            patch["excluded_dirs"] = self._normalize_exclusion_list(excluded_dirs)
        if excluded_files is not None:
            patch["excluded_files"] = self._normalize_exclusion_list(excluded_files)
        if custom_excluded is not None:
            if isinstance(custom_excluded, str):
                custom_excluded = [custom_excluded]
            if isinstance(custom_excluded, (list, set, tuple)):
                patch["custom_excluded"] = [
                    str(x)
                    for x in custom_excluded
                    if isinstance(x, str) and x.strip()
                ]
            else:
                patch["custom_excluded"] = []
        if patch:
            self.update_config(patch)
