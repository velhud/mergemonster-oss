from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List
from ...models import Category

ProgressCb = Callable[[float, str], None]

@dataclass(frozen=True)
class ScanOptions:
    include_all: bool
    ignore_binary: bool
    max_file_size_mb: float
    sort_by: str
    sort_desc: bool
    max_depth: int
    follow_symlinks: bool
    use_git_files: bool
    git_include_untracked: bool
    # Optional/peripheral scan controls (defaulted for backwards compatibility)
    token_workers: int = 0
    exclude_node_modules: bool = True
    exclude_ios_noise: bool = True
    exclude_python_noise: bool = False
    exclude_java_gradle_noise: bool = False
    exclude_web_build_noise: bool = False
    git_since_ref: str = ""
    git_until_ref: str = "HEAD"
    git_only_changed: bool = False
    exclude_vendored: bool = False

@dataclass(frozen=True)
class FilterOptions:
    category_mask: Dict[Category, bool]
    token_min: int
    token_max: int
    size_min_kb: float
    size_max_kb: float
    name_pattern: str
    name_glob: bool
    content_search: str
    content_regex: bool
    content_case: bool
    content_max_kb: int
    date_after_ts: float
    date_before_ts: float
    hide_duplicates: bool
    mark_duplicates: bool
    apply_token_filters_to_transforms: bool
    enumerate_lines: bool
    remove_empty: bool
    xcode_target: str  # "(All)" or target name
    always_include_docs: bool
    always_include_list: List[str]
    duplicate_hash_max_kb: int = 0

@dataclass(frozen=True)
class OutputOptions:
    include_tree: bool
    json_manifest: bool
    custom_start: str
    custom_end: str
    tree_start: str
    tree_end: str
    markdown_fences: bool
    markdown_toc: bool
    footer_final_only: bool

@dataclass(frozen=True)
class MergeStructure:
    mode: str              # "single_file" | "split_lines" | "split_tokens"
    split_into_n: int
    token_limit: int

@dataclass(frozen=True)
class SummaryOptions:
    content_mode: str      # "code_only" | "skeleton_only" | "code_plus_skeleton" | summary/code-plus modes
    prompt_template: str
    groq_workers: int
    use_batch: bool
    batch_progress: bool

@dataclass(frozen=True)
class UiState:
    root_dir: Path
    output_file: Path
    file_types: Dict[str, bool]
    view_mode: str         # "hierarchy" | "flat"
    copy_to_clipboard: bool
    scan: ScanOptions
    filters: FilterOptions
    output: OutputOptions
    merge: MergeStructure
    summary: SummaryOptions
