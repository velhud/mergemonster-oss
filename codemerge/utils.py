import os
import logging
import platform
import subprocess


logger = logging.getLogger(__name__)


def humanise_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def humanise_kb(n: float) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return f"{n:.0f}"


def open_file_in_default_app(filepath: str) -> None:
    """
    Try to open the file in the user's default editor or application.
    Cross-platform approach:
    - Windows: os.startfile
    - Mac: open with 'open'
    - Linux: open with 'xdg-open'
    """
    try:
        if platform.system() == "Windows":
            os.startfile(filepath)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", filepath])
        else:
            subprocess.run(["xdg-open", filepath])
    except Exception as e:
        logging.error(f"Cannot open file {filepath} in default application: {e}")


def _copy_using_command(command: list[str], text: str, *, encoding: str = "utf-8") -> bool:
    """Best-effort copy via external command; returns True on success."""
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return False
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.debug("Clipboard command %s failed to launch: %s", command, exc)
        return False

    try:
        data = text.encode(encoding)
        proc.communicate(data, timeout=3)
        return proc.returncode == 0
    except Exception as exc:  # pragma: no cover - runtime guard
        logger.debug("Clipboard command %s failed during execution: %s", command, exc)
        return False


def copy_to_clipboard(text: str) -> None:
    """Copy ``text`` to the system clipboard with platform-specific fallbacks."""

    system = platform.system()

    # Fast path: rely on native clipboard utilities when available because
    # they persist reliably after the UI exits.
    try:
        if system == "Darwin":
            if _copy_using_command(["pbcopy"], text):
                return
        elif system == "Windows":
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            normalized = normalized.replace("\n", "\r\n")
            if _copy_using_command(["clip"], normalized, encoding="utf-16le"):
                return
        else:
            if _copy_using_command(["wl-copy"], text):
                return
            if _copy_using_command(["xclip", "-selection", "clipboard"], text):
                return
            if _copy_using_command(["xsel", "--clipboard", "--input"], text):
                return
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.debug("Clipboard command path failed: %s", exc)

    logger.warning("Failed to copy text to clipboard: no supported clipboard helper found.")
