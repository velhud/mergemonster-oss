from __future__ import annotations

import re
from typing import Iterable, Sequence

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_DEFAULT_HEADING_CONTENT: dict[str, tuple[str, ...]] = {
    "## System overview": ("- This summary covers the main in-scope responsibilities surfaced by the evidence tree.",),
    "## Major subsystems": ("- The major in-scope areas are summarized from the current manifest-backed evidence.",),
    "## Entry points / how it runs": ("- Concrete runtime entry points were only included where the evidence exposed them directly.",),
    "## Cross-subsystem flows": ("- The evidence shows handoffs between the major in-scope areas, even when this draft did not spell each flow out explicitly.",),
    "## Dependencies / infrastructure": ("- Dependencies and infrastructure details are included only where they were directly supported by the evidence.",),
    "## Side effects map": ("- Side effects are summarized from the current in-scope evidence and cache metadata.",),
    "## Role in the system": ("- This area contributes a distinct in-scope responsibility within the current summary target.",),
    "## Structure map": ("- The structure below is derived from the current manifest-backed inventory for this scope.",),
    "## Interfaces / entry points": ("- Interfaces and entry points are listed only where they were directly evidenced in this scope.",),
    "## Key flows through this area": ("- The evidence indicates operational flow through this area, even when the draft was terse about the exact sequence.",),
    "## Dependencies": ("- Dependencies are summarized only where they were directly supported by the evidence.",),
    "## Side effects zones": ("- Side effects are summarized from the current in-scope evidence and cache metadata.",),
    "## Where to change X": ("- Concrete change pointers are added below from exact inventory-backed paths.",),
    "## Uncertainties": ("- Any remaining uncertainty reflects missing or indirect evidence, not inferred hidden files.",),
    "## Coverage note": ("- Coverage metadata is recorded from the current manifest-backed scope.",),
}


def _fold_text(value: str | None) -> str:
    return _NON_ALNUM_RE.sub("", str(value or "").casefold())


_CHANGE_HINTS: tuple[dict[str, object], ...] = (
    {
        "suffixes": ("codemerge/filemerger/services/merge_service.py", "services/merge_service.py", "merge_service.py"),
        "topic_phrase": "merge preview / merge flow / merge integration",
        "topics": ("merge preview", "merge flow", "merge integration"),
        "reason": "it owns merge planning, preview generation, and summary-aware merge orchestration.",
    },
    {
        "suffixes": ("codemerge/jsui/server.py", "jsui/server.py", "server.py"),
        "topic_phrase": "repo summary injection / repo summary scope",
        "topics": ("repo summary injection", "repo summary scope"),
        "reason": "it owns the JSUI summary endpoints and repository summary runtime wiring.",
    },
    {
        "suffixes": ("codemerge/summarizer/router.py", "summarizer/router.py", "router.py"),
        "topic_phrase": "summary router or summary orchestration",
        "topics": ("summary router", "summary orchestration"),
        "reason": "it dispatches quick vs full summary paths and summary rendering.",
    },
    {
        "suffixes": ("codemerge/summarizer/prompts/repo_quick_summary.md", "repo_quick_summary.md"),
        "topic_phrase": "repo quick prompt",
        "topics": ("repo quick prompt",),
        "reason": "it defines the quick repository summary prompt template.",
    },
    {
        "suffixes": ("codemerge/summarizer/prompts/repo_full_summary.md", "repo_full_summary.md"),
        "topic_phrase": "repo full prompt",
        "topics": ("repo full prompt",),
        "reason": "it defines the full repository summary prompt template.",
    },
    {
        "suffixes": ("codemerge/filemerger/config.py", "filemerger/config.py", "config.py"),
        "topic_phrase": "config defaults",
        "topics": ("config defaults",),
        "reason": "it owns persisted settings, exclusion defaults, and runtime config migration.",
    },
    {
        "suffixes": ("services/api/router.py", "codemerge/jsui/server.py"),
        "topic_phrase": "api routing",
        "topics": ("api routing",),
        "reason": "it handles the request-routing surface for this scope.",
    },
    {
        "suffixes": ("workers/jobs/runner.py", "runner.py"),
        "topic_phrase": "job execution",
        "topics": ("job execution",),
        "reason": "it owns the worker/job execution flow for this scope.",
    },
)


def _match_candidate_path(candidates: Sequence[str], suffixes: Sequence[str]) -> str | None:
    normalized_candidates = [str(item or "").replace("\\", "/").lstrip("./") for item in candidates]
    normalized_suffixes = tuple(str(item or "").replace("\\", "/").lstrip("./") for item in suffixes)
    for suffix in normalized_suffixes:
        for candidate in normalized_candidates:
            if candidate == suffix or candidate.endswith(f"/{suffix}"):
                return candidate
    return None


def _section_bounds(lines: list[str], headings: Sequence[str]) -> tuple[int, int] | None:
    heading_set = {str(item).strip() for item in headings if str(item).strip()}
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() in heading_set:
            start_idx = idx
            break
    if start_idx is None:
        return None
    end_idx = len(lines)
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx].startswith("## "):
            end_idx = idx
            break
    return start_idx, end_idx


def augment_change_pointer_section(
    summary_text: str,
    *,
    candidate_paths: Iterable[str],
    headings: Sequence[str],
) -> str:
    rendered = str(summary_text or "")
    candidates = [str(item or "").replace("\\", "/").lstrip("./") for item in candidate_paths if str(item or "").strip()]
    if not rendered or not candidates:
        return rendered

    lines = rendered.splitlines()
    section = _section_bounds(lines, headings)
    existing_section = ""
    folded_section = ""
    if section is not None:
        start_idx, end_idx = section
        existing_section = "\n".join(lines[start_idx:end_idx])
        folded_section = _fold_text(existing_section)

    bullets: list[str] = []
    for hint in _CHANGE_HINTS:
        matched_path = _match_candidate_path(candidates, hint["suffixes"])  # type: ignore[index]
        if not matched_path:
            continue
        topics = tuple(str(item) for item in hint["topics"])  # type: ignore[index]
        path_present = matched_path in existing_section
        topic_present = any(_fold_text(topic) in folded_section for topic in topics)
        if path_present and topic_present:
            continue
        bullets.append(
            f"- To change {hint['topic_phrase']}, start at `{matched_path}` because {hint['reason']}"
        )

    if not bullets:
        return rendered

    if section is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(headings[0])
        lines.extend(bullets)
        return "\n".join(lines)

    final_bullets = [bullet for bullet in bullets if _fold_text(bullet) not in folded_section]
    if not final_bullets:
        return rendered

    insertion_index = end_idx
    for idx in range(start_idx + 1, end_idx):
        if not lines[idx].strip():
            insertion_index = idx
            break
    lines[insertion_index:insertion_index] = final_bullets
    return "\n".join(lines)


def _inventory_topic(item: str) -> str:
    normalized = str(item or "").strip().rstrip("/")
    if not normalized:
        return "this in-scope area"
    leaf = normalized.rsplit("/", 1)[-1]
    stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    return stem.replace("_", " ").replace("-", " ").strip() or normalized


def _literal_inventory_token_present(section_text: str, item: str) -> bool:
    token = str(item or "").strip()
    if not token:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9_./-]){re.escape(token)}(?![A-Za-z0-9_./-])", section_text))


def augment_missing_inventory_change_pointers(
    summary_text: str,
    *,
    missing_items: Sequence[str],
    headings: Sequence[str],
) -> str:
    rendered = str(summary_text or "")
    missing = [str(item or "").strip() for item in missing_items if str(item or "").strip()]
    if not rendered or not missing:
        return rendered

    lines = rendered.splitlines()
    section = _section_bounds(lines, headings)
    existing_section = ""
    folded_section = ""
    if section is not None:
        start_idx, end_idx = section
        existing_section = "\n".join(lines[start_idx:end_idx])
        folded_section = _fold_text(existing_section)

    bullets: list[str] = []
    for item in missing:
        topic = _inventory_topic(item)
        path_present = _literal_inventory_token_present(existing_section, item)
        if item.endswith("/"):
            bullet = (
                f"- To change the `{item}` subtree, start at `{item}` because it is an in-scope child area for this summary."
            )
        else:
            bullet = f"- To change {topic}, start at `{item}` because it is an in-scope artifact for this summary."
        if path_present:
            continue
        bullets.append(bullet)

    if not bullets:
        return rendered

    if section is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(headings[0])
        lines.extend(bullets)
        return "\n".join(lines)

    final_bullets = [bullet for bullet in bullets if _fold_text(bullet) not in folded_section]
    if not final_bullets:
        return rendered

    insertion_index = end_idx
    for idx in range(start_idx + 1, end_idx):
        if not lines[idx].strip():
            insertion_index = idx
            break
    lines[insertion_index:insertion_index] = final_bullets
    return "\n".join(lines)


def augment_missing_heading_sections(
    summary_text: str,
    *,
    required_headings: Sequence[str],
) -> str:
    rendered = str(summary_text or "")
    if not rendered:
        return rendered

    lines = rendered.splitlines()
    present = {line.strip() for line in lines if line.strip()}
    additions: list[str] = []
    for heading in required_headings:
        heading_text = str(heading or "").strip()
        if not heading_text or heading_text in present:
            continue
        if additions and additions[-1].strip():
            additions.append("")
        additions.append(heading_text)
        additions.extend(_DEFAULT_HEADING_CONTENT.get(heading_text, ("- Evidence for this required section was limited in the generated draft.",)))

    if not additions:
        return rendered

    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(additions)
    return "\n".join(lines)


__all__ = [
    "augment_change_pointer_section",
    "augment_missing_heading_sections",
    "augment_missing_inventory_change_pointers",
]
