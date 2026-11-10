from __future__ import annotations

from pathlib import Path


EXTENSION_LANGUAGES: dict[str, str] = {
    "py": "python",
    "pyi": "python",
    "pyx": "python",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "go": "go",
    "java": "java",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hxx": "cpp",
    "cs": "c_sharp",
    "rs": "rust",
    "rb": "ruby",
    "php": "php",
    "swift": "swift",
    "kt": "kotlin",
    "kts": "kotlin",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "html": "html",
    "htm": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
}


def detect_language(path: str | Path, content: str | None = None) -> tuple[str, float]:
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix in EXTENSION_LANGUAGES:
        return EXTENSION_LANGUAGES[suffix], 0.95

    first_line = ""
    if content:
        first_line = content.splitlines()[0].strip().lower() if content.splitlines() else ""
    if first_line.startswith("#!") and "python" in first_line:
        return "python", 0.75
    if first_line.startswith("#!") and ("bash" in first_line or "sh" in first_line):
        return "bash", 0.75
    return "unknown", 0.0
