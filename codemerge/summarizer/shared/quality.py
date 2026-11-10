from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

_PATH_TOKEN_RE = re.compile(r"`([^`\n]+)`|(?<![A-Za-z0-9_])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?)(?![A-Za-z0-9_])")
_PATH_FRAGMENT_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?)(?![A-Za-z0-9_])")
_HUMANIZE_SUFFIX_RE = re.compile(r"\.(py|pyi|js|jsx|ts|tsx|json|toml|yaml|yml|md|txt|css|scss|html|xml|sh)$", re.IGNORECASE)
_FILE_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_BACKTICK_TEMPLATE_PLACEHOLDER_RE = re.compile(r"`\{\{([A-Za-z0-9_][A-Za-z0-9_ ]{0,64})\}\}`")
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_][A-Za-z0-9_ ]{0,64})\}\}")
_NON_PATH_SLASH_PHRASES = {"fresh/stale/missing"}
_PLATFORM_SLASH_SEGMENTS = {"android", "ios", "linux", "macos", "unix", "windows"}


def _normalize_path_token(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_trailing = raw.endswith("/")
    raw = raw.strip("`'\".,:;()[]{}")
    raw = raw.replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.lstrip("/")
    if not raw:
        return ""
    if keep_trailing and not raw.endswith("/"):
        raw = raw + "/"
    return raw


def _build_valid_path_tokens(
    *,
    inventory_items: Sequence[str],
    manifest_paths: Sequence[str],
    scope_path_prefix: str,
) -> set[str]:
    valid: set[str] = set()
    prefix = _normalize_path_token(scope_path_prefix).rstrip("/")
    for item in inventory_items:
        normalized = _normalize_path_token(item)
        if not normalized:
            continue
        valid.add(normalized)
        if prefix:
            valid.add(f"{prefix}/{normalized}")
    for item in manifest_paths:
        normalized = _normalize_path_token(item)
        if not normalized:
            continue
        valid.add(normalized)
        parts = [part for part in normalized.rstrip("/").split("/") if part]
        for depth in range(1, len(parts)):
            folder_token = "/".join(parts[:depth]) + "/"
            valid.add(folder_token)
        if prefix and normalized.startswith(f"{prefix}/"):
            valid.add(normalized[len(prefix) + 1 :])
            rel_parts = [part for part in normalized[len(prefix) + 1 :].rstrip("/").split("/") if part]
            for depth in range(1, len(rel_parts)):
                folder_token = "/".join(parts[:depth]) + "/"
                valid.add(folder_token)
    return valid


def _build_known_path_roots(
    *,
    inventory_items: Sequence[str],
    manifest_paths: Sequence[str],
    scope_path_prefix: str,
) -> set[str]:
    roots: set[str] = set()
    prefix = _normalize_path_token(scope_path_prefix).rstrip("/")
    if prefix:
        roots.add(prefix.split("/", 1)[0])
        roots.add(prefix.rsplit("/", 1)[-1])
    for item in (*inventory_items, *manifest_paths):
        normalized = _normalize_path_token(item).rstrip("/")
        if not normalized:
            continue
        first = normalized.split("/", 1)[0]
        if first:
            roots.add(first)
    return roots


def _is_unambiguous_valid_path_alias(token: str, *, valid_paths: set[str]) -> bool:
    normalized = _normalize_path_token(token)
    if not normalized:
        return False
    if normalized in valid_paths:
        return True

    wildcard_base = normalized.split("*", 1)[0].rstrip("/")
    if wildcard_base:
        if wildcard_base in valid_paths or f"{wildcard_base}/" in valid_paths:
            return True

    base = normalized.rstrip("/")
    if not base:
        return False
    if f"{base}/" in valid_paths:
        return True
    if any(candidate.startswith(f"{base}/") for candidate in valid_paths):
        return True

    file_candidates = {candidate for candidate in valid_paths if candidate.startswith(f"{base}.")}
    return len(file_candidates) == 1


def _looks_like_repo_path_candidate(token: str, *, known_roots: set[str]) -> bool:
    normalized = _normalize_path_token(token)
    if not normalized or "/" not in normalized:
        return False
    lower = normalized.lower()
    if "://" in normalized or lower.startswith(("http/", "https/")):
        return False
    first = normalized.split("/", 1)[0]
    if "." in first and first not in known_roots:
        return False
    if first in known_roots:
        return True
    base = normalized.rstrip("/")
    segments = [segment for segment in base.split("/") if segment]
    if normalized.endswith("/") or len(segments) >= 3:
        return True
    last = segments[-1] if segments else ""
    if _FILE_SUFFIX_RE.search(last):
        return True
    return any(any(marker in segment for marker in ("_", "-", ".")) for segment in segments)


def _should_ignore_route_like_token(
    *,
    raw_token: str,
    normalized: str,
    known_roots: set[str],
    leading_slash: bool,
) -> bool:
    if not leading_slash:
        return False
    first = normalized.split("/", 1)[0] if normalized else ""
    if first in known_roots:
        return False
    if first in {"api", "static"}:
        return True
    last = normalized.rstrip("/").split("/")[-1] if normalized else ""
    return not bool(_FILE_SUFFIX_RE.search(last))


def _repo_path_exists(repo_root_abs: str | None, token: str, *, scope_path_prefix: str = "") -> bool:
    if not isinstance(repo_root_abs, str) or not repo_root_abs.strip():
        return False
    normalized = _normalize_path_token(token)
    if not normalized:
        return False
    root = Path(repo_root_abs).resolve()
    candidates = [root / Path(normalized)]
    prefix_root = _normalize_path_token(scope_path_prefix).split("/", 1)[0]
    if prefix_root and not normalized.startswith(f"{prefix_root}/"):
        candidates.append(root / prefix_root / Path(normalized))

    def _candidate_exists(candidate: Path) -> bool:
        if candidate.exists():
            return True
        name = candidate.name
        if not name or "." in name or normalized.endswith("/"):
            return False
        try:
            matches = [path for path in candidate.parent.glob(f"{name}.*") if path.is_file()]
        except Exception:
            return False
        return len(matches) == 1

    return any(_candidate_exists(candidate) for candidate in candidates)


def _should_ignore_named_slash_phrase(token: str, *, known_roots: set[str]) -> bool:
    normalized = _normalize_path_token(token)
    if not normalized or normalized.endswith("/"):
        return False
    if normalized.lower() in _NON_PATH_SLASH_PHRASES:
        return True
    segments = [segment for segment in normalized.split("/") if segment]
    if len(segments) < 2:
        return False
    if any(segment.lower() in known_roots for segment in segments):
        return False
    if any(_FILE_SUFFIX_RE.search(segment) for segment in segments):
        return False
    if any(any(marker in segment for marker in ("_", "-", ".")) for segment in segments):
        return False
    lowered = [segment.lower() for segment in segments]
    return all(segment in _PLATFORM_SLASH_SEGMENTS for segment in lowered)


def find_invalid_path_references(
    summary_text: str | None,
    *,
    inventory_items: Sequence[str],
    manifest_paths: Sequence[str],
    scope_path_prefix: str,
    repo_root_abs: str | None = None,
    allow_existing_repo_paths_outside_scope: bool = False,
) -> list[str]:
    valid_paths = _build_valid_path_tokens(
        inventory_items=inventory_items,
        manifest_paths=manifest_paths,
        scope_path_prefix=scope_path_prefix,
    )
    known_roots = _build_known_path_roots(
        inventory_items=inventory_items,
        manifest_paths=manifest_paths,
        scope_path_prefix=scope_path_prefix,
    )
    invalid: list[str] = []
    seen: set[str] = set()
    rendered = str(summary_text or "")
    for match in _PATH_TOKEN_RE.finditer(rendered):
        candidates: list[tuple[str, bool]] = []
        if match.group(1):
            inner = match.group(1)
            for fragment in _PATH_FRAGMENT_RE.finditer(inner):
                fragment_leading_slash = fragment.start() > 0 and inner[fragment.start() - 1] == "/"
                candidates.append((fragment.group(1), fragment_leading_slash))
        elif match.group(2):
            fragment_leading_slash = match.start() > 0 and rendered[match.start() - 1] == "/"
            candidates.append((match.group(2), fragment_leading_slash))
        for raw_token, leading_slash in candidates:
            token = _normalize_path_token(raw_token)
            if not token or "/" not in token:
                continue
            if _should_ignore_route_like_token(
                raw_token=raw_token,
                normalized=token,
                known_roots=known_roots,
                leading_slash=leading_slash,
            ):
                continue
            if _should_ignore_named_slash_phrase(token, known_roots=known_roots):
                continue
            if not _looks_like_repo_path_candidate(token, known_roots=known_roots):
                continue
            if allow_existing_repo_paths_outside_scope and _repo_path_exists(
                repo_root_abs,
                token,
                scope_path_prefix=scope_path_prefix,
            ):
                continue
            if _is_unambiguous_valid_path_alias(token, valid_paths=valid_paths) or token in seen:
                continue
            seen.add(token)
            invalid.append(token)
    return invalid


_find_invalid_path_references = find_invalid_path_references


def _humanize_invalid_path_token(token: str) -> str:
    normalized = _normalize_path_token(token)
    if not normalized:
        return "this component"
    is_folder = normalized.endswith("/")
    parts: list[str] = []
    for part in normalized.rstrip("/").split("/"):
        cleaned = _HUMANIZE_SUFFIX_RE.sub("", part)
        cleaned = cleaned.replace("_", " ").replace("-", " ").replace(".", " ")
        cleaned = " ".join(fragment for fragment in cleaned.split() if fragment)
        if cleaned:
            parts.append(cleaned)
    if not parts:
        return "this component"
    if len(parts) > 1 and parts[0].lower() == "codemerge":
        parts = parts[1:]
    phrase = " ".join(parts[-3:]).strip()
    if not phrase:
        return "this component"
    suffix = " folder" if is_folder else " module"
    return f"the {phrase}{suffix}"


def _redact_invalid_path_token(_token: str) -> str:
    return "[unverified path omitted]"


def _replace_invalid_path_token(summary_text: str, token: str, replacement: str) -> str:
    rendered = str(summary_text or "")
    normalized = _normalize_path_token(token)
    if not normalized or not replacement:
        return rendered
    patterns = [
        re.compile(rf"`{re.escape(normalized)}`"),
        re.compile(rf"`/{re.escape(normalized)}`"),
        re.compile(rf"(?<![A-Za-z0-9_./-]){re.escape(normalized)}(?![A-Za-z0-9_./-])"),
        re.compile(rf"(?<![A-Za-z0-9_./-])/{re.escape(normalized)}(?![A-Za-z0-9_./-])"),
    ]
    for pattern in patterns:
        rendered = pattern.sub(replacement, rendered)
    return rendered


def sanitize_scoped_summary_paths(
    summary_text: str | None,
    *,
    inventory_items: Sequence[str],
    manifest_paths: Sequence[str],
    scope_path_prefix: str,
    repo_root_abs: str | None = None,
    allow_existing_repo_paths_outside_scope: bool = False,
    replacement_style: str = "humanize",
) -> tuple[str, list[str]]:
    rendered = str(summary_text or "")
    invalid_paths = find_invalid_path_references(
        rendered,
        inventory_items=inventory_items,
        manifest_paths=manifest_paths,
        scope_path_prefix=scope_path_prefix,
        repo_root_abs=repo_root_abs,
        allow_existing_repo_paths_outside_scope=allow_existing_repo_paths_outside_scope,
    )
    if not invalid_paths:
        return rendered, []
    sanitized = rendered
    style = str(replacement_style or "humanize").strip().lower()
    for token in invalid_paths:
        replacement = _humanize_invalid_path_token(token)
        if style == "redact":
            replacement = _redact_invalid_path_token(token)
        sanitized = _replace_invalid_path_token(sanitized, token, replacement)
    return sanitized, invalid_paths


def sanitize_template_placeholder_literals(summary_text: str | None) -> str:
    rendered = str(summary_text or "")
    if "{{" not in rendered or "}}" not in rendered:
        return rendered

    def _backtick_repl(match: re.Match[str]) -> str:
        token = str(match.group(1) or "").strip()
        if not token:
            return match.group(0)
        return f"`{token}` placeholder"

    def _plain_repl(match: re.Match[str]) -> str:
        token = str(match.group(1) or "").strip()
        if not token:
            return match.group(0)
        return f"{token} placeholder"

    rendered = _BACKTICK_TEMPLATE_PLACEHOLDER_RE.sub(_backtick_repl, rendered)
    rendered = _TEMPLATE_PLACEHOLDER_RE.sub(_plain_repl, rendered)
    return rendered


def find_template_placeholder_leaks(summary_text: str | None) -> list[str]:
    rendered = str(summary_text or "")
    found: list[str] = []
    seen: set[str] = set()
    for match in _TEMPLATE_PLACEHOLDER_RE.finditer(rendered):
        token = str(match.group(1) or "").strip()
        if token and token not in seen:
            seen.add(token)
            found.append(token)
    return found


def find_missing_required_headings(
    summary_text: str | None,
    *,
    required_headings: Sequence[str],
) -> list[str]:
    rendered = str(summary_text or "")
    lines = {line.strip() for line in rendered.splitlines() if line.strip()}
    missing: list[str] = []
    for heading in required_headings:
        heading_text = str(heading or "").strip()
        if heading_text and heading_text not in lines:
            missing.append(heading_text)
    return missing


def find_uncovered_inventory_items(
    summary_text: str | None,
    *,
    inventory_items: Sequence[str],
    scope_path_prefix: str,
) -> list[str]:
    rendered = str(summary_text or "")
    prefix = _normalize_path_token(scope_path_prefix).rstrip("/")
    uncovered: list[str] = []
    for item in inventory_items:
        normalized = _normalize_path_token(item)
        if not normalized:
            continue
        candidates = [normalized]
        if prefix:
            candidates.append(f"{prefix}/{normalized}")
        if not any(
            re.search(rf"(?<![A-Za-z0-9_./-]){re.escape(candidate)}(?![A-Za-z0-9_./-])", rendered)
            for candidate in candidates
        ):
            uncovered.append(normalized)
    return uncovered


def validate_full_summary_contract(
    summary_text: str | None,
    *,
    required_headings: Sequence[str],
    inventory_items: Sequence[str],
    scope_path_prefix: str,
) -> dict[str, object]:
    missing_headings = find_missing_required_headings(
        summary_text,
        required_headings=required_headings,
    )
    placeholder_leaks = find_template_placeholder_leaks(summary_text)
    uncovered_inventory_items = find_uncovered_inventory_items(
        summary_text,
        inventory_items=inventory_items,
        scope_path_prefix=scope_path_prefix,
    )
    return {
        "missing_headings": missing_headings,
        "placeholder_leaks": placeholder_leaks,
        "uncovered_inventory_items": uncovered_inventory_items,
        "inventory_covered": not uncovered_inventory_items,
    }


__all__ = [
    "find_invalid_path_references",
    "find_missing_required_headings",
    "find_template_placeholder_leaks",
    "find_uncovered_inventory_items",
    "sanitize_scoped_summary_paths",
    "sanitize_template_placeholder_literals",
    "validate_full_summary_contract",
]
