"""Full (coverage-preserving) folder/repo summarization pipeline."""

from __future__ import annotations

from .orchestrator import ensure_folder_summary_full, ensure_repo_summary_full

__all__ = ["ensure_folder_summary_full", "ensure_repo_summary_full"]
