"""Centralized logging utility for codemerge.

Provides module-level loggers that write to individual files within the
``log/`` directory. Loggers returned by :func:`get_logger` do not propagate to
Python's root logger to avoid flooding the terminal.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from .logging_ctx import get_run_id, get_event_writer
from .paths import state_directory

LOG_DIR = state_directory() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
_ROOT_LOG_FILE = LOG_DIR / "__main__.log"


class RunIdFilter(logging.Filter):
    """Inject the current run_id into each log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - simple
        try:
            record.run_id = get_run_id()
        except Exception:
            record.run_id = "-"
        return True


_SECRET_RE = re.compile(
    r"(?:sk-|gsk_)[A-Za-z0-9_-]{4,}"
    r"|(?<=bearer )[A-Za-z0-9_.~+/=-]{4,}"
    r"|(?:OPENAI|GROQ)_API_KEY=[^\s\"']+",
    re.IGNORECASE,
)


class RedactingFormatter(logging.Formatter):
    """Redact provider credentials after formatting messages and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return _SECRET_RE.sub("***", super().format(record))


def _ensure_root_logger() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_codemerge_root_handler", False):
            return
    handler = RotatingFileHandler(_ROOT_LOG_FILE, maxBytes=2_000_000, backupCount=5)
    handler._codemerge_root_handler = True  # type: ignore[attr-defined]
    handler.addFilter(RunIdFilter())
    formatter = RedactingFormatter("%(asctime)s %(levelname)s [run=%(run_id)s] %(message)s")
    handler.setFormatter(formatter)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Return a module-specific logger writing to ``log/<name>.log``."""

    _ensure_root_logger()

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_file = LOG_DIR / f"{name.split('.')[-1]}.log"
    handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5)
    handler.addFilter(RunIdFilter())
    formatter = RedactingFormatter("%(asctime)s %(levelname)s [run=%(run_id)s] %(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)
    # Prevent duplicate emission via the root logger.
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, name: str, **fields: object) -> None:
    """Log a structured JSON event and optionally mirror to per-run JSONL."""

    payload = {"event": name, **fields}
    message = _SECRET_RE.sub("***", json.dumps(payload))

    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        fn=__file__,
        lno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    logger.handle(record)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.__class__.__name__ == "LogCaptureHandler":  # pytest caplog
            handler.handle(record)

    writer = get_event_writer()
    if writer:
        try:  # pragma: no cover - I/O
            writer.write(message + "\n")
            writer.flush()
        except Exception:
            pass


def log_counter(logger: logging.Logger, name: str, value: int = 1, **fields: object) -> None:
    """Convenience helper to emit events that represent simple counters."""
    log_event(logger, name, count=value, **fields)


@contextmanager
def log_stage(logger: logging.Logger, name: str, **fields: object):
    """Context manager logging start/end events with duration."""

    t0 = time.perf_counter()
    log_event(logger, f"{name}_start", **fields)
    try:
        yield
    finally:
        dur = int((time.perf_counter() - t0) * 1000)
        log_event(logger, f"{name}_end", duration_ms=dur, **fields)
