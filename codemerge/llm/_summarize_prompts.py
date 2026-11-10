from __future__ import annotations

import json
import re
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from ._limits import SUMMARY_MODEL

_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Z0-9_]+)\s*}}")
_PROMPT_DIR = Path(__file__).resolve().parents[1] / "summarizer" / "prompts"


@lru_cache(maxsize=64)
def _load_prompt_file(name: str) -> str:
    path = _PROMPT_DIR / name
    return path.read_text(encoding="utf-8")


def _render_template(template: str, values: dict) -> str:
    missing: set[str] = set()

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values or values.get(key) is None:
            missing.add(key)
            return match.group(0)
        return str(values[key])

    rendered = _PLACEHOLDER_RE.sub(_replace, template)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"Missing template values for placeholders: {missing_list}")
    return rendered.strip()


SYSTEM_PROMPT = _load_prompt_file("system.md")

FILE_SUMMARY_LONG_TEMPLATE = _load_prompt_file("file_summary_long.md")
FILE_SUMMARY_SHORT_TEMPLATE = _load_prompt_file("file_summary_short.md")
FILE_CHUNK_LONG_TEMPLATE = _load_prompt_file("file_chunk_long.md")
FILE_CHUNK_SHORT_TEMPLATE = _load_prompt_file("file_chunk_short.md")
FILE_MERGE_LONG_TEMPLATE = _load_prompt_file("file_merge_long.md")
FILE_MERGE_SHORT_TEMPLATE = _load_prompt_file("file_merge_short.md")

GROUP_SUMMARY_FULL_TEMPLATE = _load_prompt_file("group_summary_full.md")
FOLDER_FULL_TEMPLATE = _load_prompt_file("folder_full_summary.md")
FOLDER_QUICK_TEMPLATE = _load_prompt_file("folder_quick_summary.md")
REPO_FULL_TEMPLATE = _load_prompt_file("repo_full_summary.md")
REPO_QUICK_TEMPLATE = _load_prompt_file("repo_quick_summary.md")

QUICK_EVIDENCE_FOLDER_TEMPLATE = _load_prompt_file("quick_evidence_folder.md")
QUICK_EVIDENCE_REPO_TEMPLATE = _load_prompt_file("quick_evidence_repo.md")

# Prompt used for comparing two versions of the same file. The model must
# respond with a single keyword indicating which file to keep or if there is
# no meaningful difference. This concise answer makes it easier to filter
# results programmatically.
COMPARE_PROMPT = (
    "You will receive two versions of the same source file. "
    "Reply ONLY with one of these keywords: NO_DIFFERENCE, USE_A, USE_B, UNSURE.\n\n"
    "FILE A START\n{file_a}\nFILE A END\n\n"
    "FILE B START\n{file_b}\nFILE B END"
)

PLAIN_FOLDER_PROMPT_TEMPLATE = (
    "Summarize what this folder does based on these file and subfolder summaries. "
    "Plain text. Do not invent.\n\n"
    "FOLDER: {folder_rel}\n"
    "EVIDENCE:\n{evidence}\n"
    "END EVIDENCE"
)

PLAIN_REPO_PROMPT_TEMPLATE = (
    "Summarize what this repository does based on these top-level folder and file summaries. "
    "Plain text. Do not invent.\n\n"
    "REPO: {repo_name}\n"
    "EVIDENCE:\n{evidence}\n"
    "END EVIDENCE"
)

PLAIN_SUMMARY_VERSION = 2


def file_prompt_fingerprint_payload(kind: str | None = None) -> dict:
    if kind is None:
        return {
            "system_prompt": SYSTEM_PROMPT,
            "file_summary_long": FILE_SUMMARY_LONG_TEMPLATE,
            "file_summary_short": FILE_SUMMARY_SHORT_TEMPLATE,
            "file_chunk_long": FILE_CHUNK_LONG_TEMPLATE,
            "file_chunk_short": FILE_CHUNK_SHORT_TEMPLATE,
            "file_merge_long": FILE_MERGE_LONG_TEMPLATE,
            "file_merge_short": FILE_MERGE_SHORT_TEMPLATE,
            "chunk_tokens": 20_000,
        }
    short = str(kind or "").lower().startswith("short")
    return {
        "system_prompt": SYSTEM_PROMPT,
        "file_summary": FILE_SUMMARY_SHORT_TEMPLATE if short else FILE_SUMMARY_LONG_TEMPLATE,
        "file_chunk": FILE_CHUNK_SHORT_TEMPLATE if short else FILE_CHUNK_LONG_TEMPLATE,
        "file_merge": FILE_MERGE_SHORT_TEMPLATE if short else FILE_MERGE_LONG_TEMPLATE,
        "chunk_tokens": 20_000,
    }


def file_prompt_fingerprint(kind: str, version: int, *, model: str | None = None) -> str:
    payload = {
        "model": str(model or SUMMARY_MODEL),
        "kind": "short" if str(kind).lower().startswith("short") else "long",
        "prompt_rules": file_prompt_fingerprint_payload(kind),
        "version": int(version or 1),
        "plain_text": True,
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def aggregate_prompt_fingerprint(kind: str, mode: str, version: int) -> str:
    mode_norm = str(mode or "full").strip().lower()
    kind_norm = str(kind or "").strip().lower()
    if kind_norm not in {"folder", "repo", "group"}:
        raise ValueError(f"invalid aggregate kind: {kind!r}")
    if mode_norm not in {"full", "quick"} and kind_norm != "group":
        raise ValueError(f"invalid aggregate mode: {mode!r}")
    if kind_norm == "group":
        template = GROUP_SUMMARY_FULL_TEMPLATE
    elif kind_norm == "repo":
        template = REPO_QUICK_TEMPLATE if mode_norm == "quick" else REPO_FULL_TEMPLATE
    else:
        template = FOLDER_QUICK_TEMPLATE if mode_norm == "quick" else FOLDER_FULL_TEMPLATE
    selector_template = None
    if kind_norm in {"folder", "repo"} and mode_norm == "quick":
        selector_template = QUICK_EVIDENCE_REPO_TEMPLATE if kind_norm == "repo" else QUICK_EVIDENCE_FOLDER_TEMPLATE
    payload = {
        "kind": kind_norm,
        "mode": mode_norm,
        "version": int(version or 1),
        "template": template,
        "selector_template": selector_template,
        "system_prompt": SYSTEM_PROMPT,
        "plain_text": True,
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def plain_prompt_fingerprint(kind: str, version: int) -> str:
    return aggregate_prompt_fingerprint(kind, "full", version)


def build_file_summary_prompt(
    kind: str,
    *,
    profile: str,
    repo_name: str,
    file_rel: str,
    file_abs: str,
    file_ext: str,
    file_size_bytes: int,
    file_content: str,
) -> str:
    short = str(kind or "").lower().startswith("short")
    template = FILE_SUMMARY_SHORT_TEMPLATE if short else FILE_SUMMARY_LONG_TEMPLATE
    return _render_template(
        template,
        {
            "PROFILE": profile,
            "REPO_NAME": repo_name,
            "FILE_REL": file_rel,
            "FILE_ABS": file_abs,
            "FILE_EXT": file_ext,
            "FILE_SIZE_BYTES": str(int(file_size_bytes)),
            "FILE_CONTENT": file_content,
        },
    )


def build_file_chunk_prompt(
    kind: str,
    *,
    profile: str,
    repo_name: str,
    file_rel: str,
    chunk_index: int,
    chunk_total: int,
    chunk_hint: str,
    chunk_content: str,
) -> str:
    short = str(kind or "").lower().startswith("short")
    template = FILE_CHUNK_SHORT_TEMPLATE if short else FILE_CHUNK_LONG_TEMPLATE
    return _render_template(
        template,
        {
            "PROFILE": profile,
            "REPO_NAME": repo_name,
            "FILE_REL": file_rel,
            "CHUNK_INDEX": str(int(chunk_index)),
            "CHUNK_TOTAL": str(int(chunk_total)),
            "CHUNK_HINT": chunk_hint,
            "FILE_CHUNK_CONTENT": chunk_content,
        },
    )


def build_file_merge_prompt(
    kind: str,
    *,
    profile: str,
    repo_name: str,
    file_rel: str,
    chunk_total: int,
    chunk_extract_blocks: str,
) -> str:
    short = str(kind or "").lower().startswith("short")
    template = FILE_MERGE_SHORT_TEMPLATE if short else FILE_MERGE_LONG_TEMPLATE
    return _render_template(
        template,
        {
            "PROFILE": profile,
            "REPO_NAME": repo_name,
            "FILE_REL": file_rel,
            "CHUNK_TOTAL": str(int(chunk_total)),
            "CHUNK_EXTRACT_BLOCKS": chunk_extract_blocks,
        },
    )


def build_group_summary_prompt(
    *,
    profile: str,
    repo_name: str,
    scope_rel: str,
    layer_index: int,
    group_id: str,
    inventory_list: str,
    evidence_blocks: str,
) -> str:
    return _render_template(
        GROUP_SUMMARY_FULL_TEMPLATE,
        {
            "PROFILE": profile,
            "REPO_NAME": repo_name,
            "SCOPE_REL": scope_rel,
            "LAYER_INDEX": str(int(layer_index)),
            "GROUP_ID": str(group_id),
            "INVENTORY_LIST_FOR_SCOPE": inventory_list,
            "EVIDENCE_BLOCKS": evidence_blocks,
        },
    )


def build_folder_summary_prompt(
    mode: str,
    *,
    profile: str,
    repo_name: str,
    folder_rel: str,
    manifest_id: str,
    scope_label: str,
    scope_def_version: int,
    inventory_list: str,
    tree_snapshot: str,
    anchor_blocks: str,
    evidence_blocks: str,
) -> str:
    mode_norm = str(mode or "full").strip().lower()
    template = FOLDER_QUICK_TEMPLATE if mode_norm == "quick" else FOLDER_FULL_TEMPLATE
    return _render_template(
        template,
        {
            "PROFILE": profile,
            "REPO_NAME": repo_name,
            "FOLDER_REL": folder_rel,
            "MANIFEST_ID": manifest_id,
            "SCOPE_LABEL": scope_label,
            "SCOPE_DEF_VERSION": str(int(scope_def_version)),
            "INVENTORY_LIST": inventory_list,
            "TREE_SNAPSHOT": tree_snapshot,
            "ANCHOR_BLOCKS": anchor_blocks,
            "EVIDENCE_BLOCKS": evidence_blocks,
        },
    )


def build_repo_summary_prompt(
    mode: str,
    *,
    profile: str,
    repo_name: str,
    manifest_id: str,
    scope_label: str,
    scope_def_version: int,
    inventory_list: str,
    tree_snapshot: str,
    anchor_blocks: str,
    evidence_blocks: str,
) -> str:
    mode_norm = str(mode or "full").strip().lower()
    template = REPO_QUICK_TEMPLATE if mode_norm == "quick" else REPO_FULL_TEMPLATE
    return _render_template(
        template,
        {
            "PROFILE": profile,
            "REPO_NAME": repo_name,
            "MANIFEST_ID": manifest_id,
            "SCOPE_LABEL": scope_label,
            "SCOPE_DEF_VERSION": str(int(scope_def_version)),
            "INVENTORY_LIST": inventory_list,
            "TREE_SNAPSHOT": tree_snapshot,
            "ANCHOR_BLOCKS": anchor_blocks,
            "EVIDENCE_BLOCKS": evidence_blocks,
        },
    )


def build_quick_selector_prompt(
    target_kind: str,
    *,
    repo_name: str,
    folder_rel: str | None,
    n_picks: int,
    inventory_list: str,
    child_stats: str,
) -> str:
    kind = str(target_kind or "").strip().lower()
    if kind == "repo":
        template = QUICK_EVIDENCE_REPO_TEMPLATE
    elif kind == "folder":
        template = QUICK_EVIDENCE_FOLDER_TEMPLATE
    else:
        raise ValueError(f"invalid target_kind for selector: {target_kind!r}")
    return _render_template(
        template,
        {
            "REPO_NAME": repo_name,
            "FOLDER_REL": folder_rel or "",
            "N_PICKS": str(int(n_picks)),
            "INVENTORY_LIST": inventory_list,
            "CHILD_STATS": child_stats,
        },
    )


def build_plain_summary_prompt(
    kind: str,
    *,
    folder_rel: str,
    repo_name: str,
    evidence: str,
) -> str:
    if kind == "repo":
        return PLAIN_REPO_PROMPT_TEMPLATE.format(
            repo_name=repo_name,
            evidence=evidence,
        )
    return PLAIN_FOLDER_PROMPT_TEMPLATE.format(
        folder_rel=folder_rel,
        evidence=evidence,
    )


def _escape_braces(text: str) -> str:
    """Return ``text`` with braces doubled so ``str.format`` preserves them."""
    return text.replace("{", "{{").replace("}", "}}")
