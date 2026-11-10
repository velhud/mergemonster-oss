from __future__ import annotations

import concurrent.futures
from datetime import datetime
from typing import Dict, List

from codemerge.logger import get_logger

from .restore import collect_source_files
from ..types import FileProcessorLike as FileProcessor

logger = get_logger(__name__)


def compare_codebases_ai(
    processor: "FileProcessor",
    src_a: str,
    src_b: str,
) -> Dict[str, str]:
    """Compare two codebases file-by-file using the AI summarizer."""

    logger.info("compare_codebases_ai start: %s vs %s", src_a, src_b)

    from codemerge.llm.summarize import compare_files_ai

    files_a = collect_source_files(processor, src_a)
    files_b = collect_source_files(processor, src_b)

    results: Dict[str, str] = {}
    all_keys = sorted(set(files_a.keys()) | set(files_b.keys()))

    pending: List[str] = []

    for rel in all_keys:
        if rel not in files_a:
            results[rel] = "ONLY_B"
        elif rel not in files_b:
            results[rel] = "ONLY_A"
        else:
            pending.append(rel)

    max_workers = min(len(pending), 100) if pending else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_rel = {
            executor.submit(compare_files_ai, files_a[rel], files_b[rel]): rel
            for rel in pending
        }
        for future in concurrent.futures.as_completed(future_to_rel):
            rel = future_to_rel[future]
            try:
                results[rel] = future.result()
            except Exception as exc:  # pragma: no cover - best effort logging
                results[rel] = f"ERROR: {exc}"

    return results


def format_comparison_report(
    results: Dict[str, str],
    *,
    src_a: str,
    src_b: str,
) -> str:
    """Render a human-readable summary of :func:`compare_codebases_ai` results."""

    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    lines: List[str] = [
        "Codebase Comparison Report",
        f"Source A: {src_a}",
        f"Source B: {src_b}",
        f"Generated: {timestamp}",
        "",
    ]

    only_a: List[str] = []
    only_b: List[str] = []
    errors: Dict[str, str] = {}
    mismatches: Dict[str, str] = {}

    for path in sorted(results):
        verdict = results[path]
        if verdict == "ONLY_A":
            only_a.append(path)
        elif verdict == "ONLY_B":
            only_b.append(path)
        elif verdict.startswith("ERROR:"):
            errors[path] = verdict
        else:
            mismatches[path] = verdict

    if only_a:
        lines.append("Files only present in Source A:")
        lines.extend(f"  - {path}" for path in only_a)
        lines.append("")

    if only_b:
        lines.append("Files only present in Source B:")
        lines.extend(f"  - {path}" for path in only_b)
        lines.append("")

    for path, verdict in mismatches.items():
        lines.append(f"--- {path}")
        text = verdict.strip()
        if text:
            lines.append(text)
        else:
            lines.append("(no details returned by comparer)")
        lines.append("")

    if errors:
        lines.append("Files that failed comparison:")
        for path, verdict in errors.items():
            lines.append(f"  - {path}: {verdict}")
        lines.append("")

    # Ensure trailing newline for nicer file output
    if lines and lines[-1] != "":
        lines.append("")

    return "\n".join(lines)
