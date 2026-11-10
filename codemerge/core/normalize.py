from __future__ import annotations

import ast
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterator, Literal, Optional, Set, Tuple
from urllib.parse import urlparse, unquote


_INVISIBLE_CHARS = {
    "\ufeff",  # BOM
    "\u200b",
    "\u200c",
    "\u200d",
    "\u200e",
    "\u200f",
    "\u00a0",
}


Status = Literal[
    "kept",
    "corrected_fuzzy",
    "corrected_llm",
    "ignored",
    "invalid",
    "missed",
]


def canonical_path(path: str) -> str:
    """Return a stable absolute path for cache keys and comparisons."""
    try:
        resolved = os.path.realpath(os.path.expanduser(path))
    except Exception:
        resolved = os.path.abspath(path)
    return os.path.normcase(resolved)


@dataclass(slots=True)
class NormalizeItem:
    raw: str
    cleaned: Optional[str]
    rel: Optional[str]
    status: Status
    note: Optional[str] = None


@dataclass(slots=True)
class NormalizeResult:
    items: list[NormalizeItem]
    requested_rel: set[str]
    stats: Dict[str, int]
    cleaned_lines: list[str]


def sanitize_pasted_paths(
    raw: str,
    root_dir: str,
    scanned_rel: Set[str],
    *,
    smart: bool,
    autocorrect: bool,
    use_llm: bool,
    dir_prefix: bool = False,
) -> NormalizeResult:
    """Normalise user input into a set of root-relative paths ready for matching."""

    norm_text = _preprocess_text(raw or "", smart=smart)
    entries = list(_extract_entries(norm_text, smart=smart))

    root_abs = os.path.realpath(os.path.expanduser(root_dir))
    case_sensitive = _detect_case_sensitivity(root_abs, scanned_rel)
    scanned_lookup = {path: path for path in scanned_rel}
    scanned_casefold = {path.casefold(): path for path in scanned_rel} if not case_sensitive else {}

    basename_index: Dict[Tuple[str, int], list[str]] = {}
    depth_index: Dict[int, list[str]] = {}
    for rel in scanned_rel:
        key = (os.path.basename(rel), rel.count("/"))
        basename_index.setdefault(key, []).append(rel)
        depth_index.setdefault(rel.count("/"), []).append(rel)
    suffix_index: Dict[str, list[str]] = {}
    if smart:
        for rel in scanned_rel:
            parts = rel.split("/")
            for idx in range(len(parts)):
                suffix = "/".join(parts[idx:])
                suffix_index.setdefault(suffix, []).append(rel)

    items: list[NormalizeItem] = []
    requested: set[str] = set()
    stats: Dict[str, int] = {}

    pending_llm: list[int] = []

    for raw_line in entries:
        item = NormalizeItem(raw=raw_line, cleaned=None, rel=None, status="ignored", note=None)
        cleaned_line = _clean_line(raw_line, smart=smart)

        if cleaned_line is None:
            _bump(stats, "ignored")
            item.note = "blank/comment"
            items.append(item)
            continue

        candidate_path, note = _resolve_special_formats(cleaned_line, smart=smart)
        if candidate_path is None:
            candidate_path = cleaned_line

        candidate_path = _finalise_candidate(candidate_path, scanned_rel)

        rel_path = _normalise_to_rel(candidate_path, root_abs)

        item.cleaned = rel_path or candidate_path

        if rel_path is None:
            item.status = "invalid"
            item.note = note or "outside root"
            _bump(stats, "invalid")
            items.append(item)
            continue

        # Check exact match first.
        resolved, case_note = _match_exact(rel_path, scanned_lookup, scanned_casefold, case_sensitive)
        item_note = _merge_notes(note, case_note)
        if resolved is None and smart:
            trimmed_rel, trim_note = _strip_trailing_annotation(rel_path)
            if trimmed_rel != rel_path:
                rel_path = trimmed_rel
                item.cleaned = rel_path
                resolved, case_note = _match_exact(rel_path, scanned_lookup, scanned_casefold, case_sensitive)
                item_note = _merge_notes(note, trim_note, case_note)

        if resolved is not None:
            if resolved in requested:
                item.status = "ignored"
                item.note = _merge_notes(item_note, "duplicate")
                _bump(stats, "ignored")
            else:
                requested.add(resolved)
                item.status = "kept"
                item.rel = resolved
                item.note = item_note
                _bump(stats, "kept")
            items.append(item)
            continue

        # Directory prefix handling (only when requested).
        if dir_prefix:
            pref_matches = _collect_prefix_matches(rel_path, scanned_rel)
            if pref_matches:
                additions = [p for p in pref_matches if p not in requested]
                for rel in additions:
                    requested.add(rel)
                item.status = "kept"
                item.rel = rel_path
                item.note = _merge_notes(item_note, f"directory prefix ({len(additions)} added)")
                _bump(stats, "kept")
                items.append(item)
                continue

        if smart:
            subpath_match, subpath_note = _match_by_suffix(rel_path, suffix_index)
            if subpath_match:
                if subpath_match not in requested:
                    requested.add(subpath_match)
                    item.status = "corrected_fuzzy"
                    item.rel = subpath_match
                    item.note = _merge_notes(item_note, subpath_note or "matched by suffix")
                    _bump(stats, "corrected_fuzzy")
                else:
                    item.status = "ignored"
                    item.note = _merge_notes(item_note, "duplicate after correction", subpath_note)
                    _bump(stats, "ignored")
                items.append(item)
                continue
            if subpath_note:
                item_note = _merge_notes(item_note, subpath_note)

        corrected = None
        if autocorrect:
            corrected = _fuzzy_match(
                rel_path,
                basename_index,
                depth_index,
                threshold=0.94,
                margin=0.03,
            )
        if corrected is not None and corrected in scanned_rel:
            if corrected not in requested:
                requested.add(corrected)
                item.status = "corrected_fuzzy"
                item.rel = corrected
                item.note = _merge_notes(item_note, "fuzzy autocorrect")
                _bump(stats, "corrected_fuzzy")
            else:
                item.status = "ignored"
                item.note = _merge_notes(item_note, "duplicate after correction")
                _bump(stats, "ignored")
            items.append(item)
            continue

        # Mark for possible LLM assist.
        if use_llm:
            item.status = "missed"
            item.note = item_note or "no match"
            items.append(item)
            pending_llm.append(len(items) - 1)
            continue

        item.status = "missed"
        item.note = item_note or "not in scan"
        _bump(stats, "missed")
        items.append(item)

    if use_llm and pending_llm:
        applied = _llm_assist(
            items,
            pending_llm,
            scanned_rel,
            requested,
            basename_index,
            depth_index,
        )
        for status in applied:
            _bump(stats, status)

        # Update missed stats for remaining misses that LLM could not resolve.
        remaining_missed = sum(1 for idx in pending_llm if items[idx].status == "missed")
        if remaining_missed:
            stats["missed"] = stats.get("missed", 0) + remaining_missed

    cleaned_lines = sorted(requested)
    return NormalizeResult(items=items, requested_rel=requested, stats=stats, cleaned_lines=cleaned_lines)


def _preprocess_text(text: str, *, smart: bool) -> str:
    if not text:
        return ""
    for ch in _INVISIBLE_CHARS:
        text = text.replace(ch, "")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if smart:
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                continue
            lines.append(line)
        text = "\n".join(lines)
    return text


def _extract_entries(text: str, *, smart: bool) -> Iterator[str]:
    stripped = text.strip()
    if not stripped:
        return iter(())

    # Structured formats first when smart cleanup is enabled.
    if smart:
        json_lines = _try_parse_json_list(stripped)
        if json_lines is not None:
            return iter(json_lines)

        yaml_lines = _try_parse_yaml_list(text)
        if yaml_lines is not None:
            return iter(yaml_lines)

        python_lines = _try_parse_python_list(stripped)
        if python_lines is not None:
            return iter(python_lines)

    if "\n" not in text and smart and any(sep in text for sep in (",", ";")):
        return (part.strip() for part in re.split(r"[;,]", text) if part.strip())

    return (line for line in text.split("\n"))


def _clean_line(line: str, *, smart: bool) -> Optional[str]:
    if line is None:
        return None
    original = line
    line = line.strip()
    if not line:
        return None

    if smart:
        if line.startswith("#") or line.startswith("//"):
            return None
        line = re.sub(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)", "", line)

    line = line.strip()
    if not line:
        return None

    line = _strip_wrappers(line)

    if smart and " | " in line:
        line = line.split(" | ", 1)[0].strip()

    if smart and line.lower().startswith("diff --git "):
        parts = line.split()
        for token in reversed(parts):
            if token.startswith("b/") and len(token) > 2:
                line = token[2:]
                break
            if token.startswith("a/") and len(token) > 2:
                line = token[2:]
                break

    if smart and line.startswith(("+++ ", "--- ")):
        token = line.split(maxsplit=1)[-1]
        if token.startswith("a/") or token.startswith("b/"):
            token = token[2:]
        line = token

    return line.strip() or original.strip()


def _strip_wrappers(text: str) -> str:
    pairs = [
        ("'", "'"),
        ('"', '"'),
        ("“", "”"),
        ("‘", "’"),
        ("`", "`"),
        ("(", ")"),
        ("[", "]"),
        ("{", "}"),
        ("<", ">"),
    ]
    changed = True
    text = text.strip()
    while changed and text:
        changed = False
        for left, right in pairs:
            if text.startswith(left) and text.endswith(right) and len(text) >= len(left) + len(right):
                text = text[len(left) : len(text) - len(right)].strip()
                changed = True
    return text


def _resolve_special_formats(line: str, *, smart: bool) -> Tuple[Optional[str], Optional[str]]:
    if smart:
        parsed = urlparse(line)
        if parsed.scheme in {"http", "https"} and parsed.netloc.endswith("github.com"):
            parts = parsed.path.split("/blob/", 1)
            if len(parts) == 2:
                path_part = parts[1].split("/", 1)
                if len(path_part) == 2:
                    return unquote(path_part[1]), "from github url"
        if parsed.scheme == "file":
            return unquote(parsed.path), "file uri"

        if line.startswith("a/") or line.startswith("b/"):
            return line[2:], "git prefix"

        if line.lower().startswith("rename to "):
            return line.split(None, 2)[-1], "rename target"

        match = re.match(r"(.+?):\d+(?::\d+)?$", line)
        if match:
            return match.group(1), "stripped :line"

    return line, None


def _match_exact(
    rel_path: str,
    scanned_lookup: Dict[str, str],
    scanned_casefold: Dict[str, str],
    case_sensitive: bool,
) -> Tuple[Optional[str], Optional[str]]:
    resolved = scanned_lookup.get(rel_path)
    if resolved is not None:
        return resolved, None
    if not case_sensitive:
        resolved = scanned_casefold.get(rel_path.casefold())
        if resolved is not None:
            return resolved, "case normalised"
    return None, None


def _strip_trailing_annotation(path: str) -> Tuple[str, Optional[str]]:
    if not path:
        return path, None
    match = re.match(r"^(.*?)(?:\s*\(([^()]*)\))\s*$", path)
    if match:
        candidate = match.group(1).strip()
        if candidate:
            return candidate, "trimmed trailing note"
    for sep in (" #", " //", " ;", " - ", " : "):
        idx = path.rfind(sep)
        if idx > 0:
            candidate = path[:idx].rstrip()
            if candidate:
                return candidate, "trimmed trailing note"
    return path, None


def _match_by_suffix(rel_path: str, suffix_index: Dict[str, list[str]]) -> Tuple[Optional[str], Optional[str]]:
    if not rel_path:
        return None, None
    key = rel_path.strip("/")
    if not key:
        return None, None
    matches = suffix_index.get(key, [])
    if len(matches) == 1:
        return matches[0], "matched by suffix"
    if len(matches) > 1:
        return None, f"ambiguous suffix ({len(matches)} matches)"
    parts = key.split("/")
    for idx in range(1, len(parts)):
        candidate = "/".join(parts[idx:])
        matches = suffix_index.get(candidate, [])
        if len(matches) == 1:
            plural = "" if idx == 1 else "s"
            return matches[0], f"dropped {idx} leading segment{plural}"
        if len(matches) > 1:
            return None, f"ambiguous suffix ({len(matches)} matches)"
    return None, None


def _merge_notes(*notes: Optional[str]) -> Optional[str]:
    parts: list[str] = []
    for note in notes:
        if not note:
            continue
        cleaned = str(note).strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return "; ".join(parts) if parts else None


def _finalise_candidate(candidate: str, scanned_rel: Set[str]) -> str:
    candidate = os.path.expandvars(candidate)
    candidate = os.path.expanduser(candidate)
    candidate = candidate.replace("\\", "/")
    candidate = re.sub(r"/{2,}", "/", candidate)
    candidate = re.sub(r"\.{2,}(?=\.[^.\\/]+$)", ".", candidate)

    while candidate.endswith((",", ";", ".", ":")) and not re.match(r"^[A-Za-z]:$", candidate):
        candidate = candidate[:-1]

    if candidate in scanned_rel:
        return candidate

    return candidate


def _normalise_to_rel(path: str, root_abs: str) -> Optional[str]:
    if not path:
        return None
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(root_abs, expanded)
    try:
        real = os.path.realpath(expanded)
        root_real = os.path.realpath(root_abs)
    except Exception:
        return None

    if real == root_real:
        return None

    if not real.startswith(root_real.rstrip(os.sep) + os.sep):
        return None

    rel = os.path.relpath(real, root_real)
    return rel.replace("\\", "/")


def _collect_prefix_matches(rel_path: str, scanned_rel: Set[str]) -> list[str]:
    if not rel_path:
        return []
    prefix = rel_path.rstrip("/")
    if not prefix:
        return []
    matches = [path for path in scanned_rel if path == prefix or path.startswith(prefix + "/")]
    return matches


def _fuzzy_match(
    rel_path: str,
    basename_index: Dict[Tuple[str, int], list[str]],
    depth_index: Dict[int, list[str]],
    *,
    threshold: float,
    margin: float,
) -> Optional[str]:
    candidates = _candidate_pool(rel_path, basename_index, depth_index)
    if not candidates:
        return None

    best_score = 0.0
    best_path: Optional[str] = None

    for candidate in candidates:
        score = _ratio(rel_path, candidate)
        if score > best_score:
            best_score = score
            best_path = candidate

    if best_path is None or best_score < threshold:
        return None

    # Ensure uniqueness within margin.
    close_scores = [
        _ratio(rel_path, cand)
        for cand in candidates
        if cand != best_path
    ]
    if any(score > best_score - margin for score in close_scores):
        return None
    return best_path


def _ratio(a: str, b: str) -> float:
    # Lightweight sequence matcher without importing difflib globally.
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def _llm_assist(
    items: list[NormalizeItem],
    indices: list[int],
    scanned_rel: Set[str],
    requested: Set[str],
    basename_index: Dict[Tuple[str, int], list[str]],
    depth_index: Dict[int, list[str]],
) -> list[str]:
    """Deterministic micro-LLM stand-in that abides by guardrails."""

    applied_status: list[str] = []
    limit = min(len(indices), 50)
    for idx in indices[:limit]:
        item = items[idx]
        if not item.cleaned:
            continue
        rel_guess = item.cleaned
        pool = _candidate_pool(rel_guess, basename_index, depth_index)
        if not pool:
            continue

        best = sorted(
            ((cand, _ratio(rel_guess, cand)) for cand in pool),
            key=lambda pair: pair[1],
            reverse=True,
        )
        if not best:
            continue

        cand, score = best[0]
        if score < 0.85:
            continue

        if any(other_score >= score - 0.01 for _, other_score in best[1:3]):
            continue

        if cand in requested:
            item.status = "ignored"
            item.note = "duplicate after llm"
            applied_status.append("ignored")
            continue

        requested.add(cand)
        item.status = "corrected_llm"
        item.rel = cand
        item.note = "llm assist"
        applied_status.append("corrected_llm")

    return applied_status


def _candidate_pool(
    rel_path: str,
    basename_index: Dict[Tuple[str, int], list[str]],
    depth_index: Dict[int, list[str]],
) -> list[str]:
    basename = os.path.basename(rel_path)
    depth = rel_path.count("/")
    candidates = list(basename_index.get((basename, depth), []))
    if candidates:
        return candidates

    ext = os.path.splitext(basename)[1]
    depth_candidates = depth_index.get(depth, [])
    if ext and depth_candidates:
        filtered = [cand for cand in depth_candidates if os.path.splitext(os.path.basename(cand))[1] == ext]
        if filtered:
            return filtered
    return list(depth_candidates)


def _detect_case_sensitivity(root_dir: str, scanned_rel: Set[str]) -> bool:
    if os.name == "nt":
        return False

    for rel in scanned_rel:
        abs_path = os.path.join(root_dir, rel)
        try:
            if not os.path.exists(abs_path):
                continue
        except Exception:
            continue
        toggled = _swapcase_component(abs_path)
        if toggled == abs_path:
            continue
        try:
            if os.path.exists(toggled):
                return False
            return True
        except Exception:
            return True
    # Fallback to normcase heuristic.
    return os.path.normcase("A") != os.path.normcase("a")


def _swapcase_component(path: str) -> str:
    base = os.path.basename(path)
    if not base:
        return path
    chars = list(base)
    for idx, ch in enumerate(chars):
        if ch.isalpha():
            chars[idx] = ch.swapcase()
            swapped = "".join(chars)
            return os.path.join(os.path.dirname(path), swapped)
    return path


def _try_parse_json_list(text: str) -> Optional[list[str]]:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
        return [item for item in payload]
    return None


def _try_parse_yaml_list(text: str) -> Optional[list[str]]:
    lines = text.split("\n")
    usable = [line for line in lines if line.strip()]
    if not usable:
        return None
    if not all(line.lstrip().startswith("- ") for line in usable):
        return None
    results = []
    for line in usable:
        entry = line.lstrip()[2:]
        entry = entry.strip()
        entry = _strip_wrappers(entry)
        if entry:
            results.append(entry)
    return results or None


def _try_parse_python_list(text: str) -> Optional[list[str]]:
    try:
        payload = ast.literal_eval(text)
    except Exception:
        return None
    if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
        return list(payload)
    return None


def _bump(stats: Dict[str, int], key: str) -> None:
    stats[key] = stats.get(key, 0) + 1
