"""SQLite-backed token count cache shared by MergeMonster components."""

from __future__ import annotations

import atexit
import sqlite3
import threading
from pathlib import Path

from ..paths import state_directory
from typing import Optional, Sequence

from codemerge.logger import get_logger

logger = get_logger(__name__)



DEFAULT_CACHE_PATH = state_directory() / "codemerge_token_cache.db"
CACHE_BATCH_SIZE = 50
UNREADABLE_SENTINEL = -1


class TokenCache:
    """Persist token counts for files on disk with minimal locking overhead."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        encoding_tag: str,
        batch_size: int = CACHE_BATCH_SIZE,
    ) -> None:
        resolved_path = db_path or (state_directory() / "codemerge_token_cache.db")
        self._path = Path(resolved_path).expanduser()
        self._encoding_tag = encoding_tag
        self._batch_size = max(1, batch_size)
        self._lock = threading.Lock()
        self._pending: list[tuple[str, int, int, int, str]] = []
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._prepare_schema()
        atexit.register(self._atexit_commit)

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def _prepare_schema(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS token_cache ("
            "abs_path TEXT PRIMARY KEY, "
            "mtime_ns INTEGER NOT NULL, "
            "size INTEGER NOT NULL, "
            "tokens INTEGER NOT NULL, "
            "encoding TEXT)"
        )
        try:
            cols = [row[1] for row in self._conn.execute("PRAGMA table_info(token_cache)")]
        except Exception as exc:
            logger.warning("Unable to introspect token_cache schema: %s", exc)
            cols = []
        if "mtime_ns" not in cols:
            try:
                self._conn.execute("ALTER TABLE token_cache ADD COLUMN mtime_ns INTEGER")
            except Exception as exc:
                logger.warning("Failed to add mtime_ns column to token_cache: %s", exc)
        if "encoding" not in cols:
            try:
                self._conn.execute("ALTER TABLE token_cache ADD COLUMN encoding TEXT")
            except Exception as exc:
                logger.warning("Failed to add encoding column to token_cache: %s", exc)
        try:
            self._conn.execute(
                "DELETE FROM token_cache WHERE encoding IS NULL OR encoding <> ? OR mtime_ns IS NULL",
                (self._encoding_tag,),
            )
        except Exception as exc:
            logger.debug("Failed to purge stale token_cache rows: %s", exc)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get(self, path: str, size: int, mtime_ns: int) -> int | None:
        """Return cached token count for ``path`` or ``None`` if missing/stale."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT tokens FROM token_cache WHERE abs_path=? AND mtime_ns=? AND size=? AND encoding=?",
                    (path, mtime_ns, size, self._encoding_tag),
                )
            except Exception as exc:
                logger.debug("Token cache lookup failed for %s: %s", path, exc)
                return None
            row = cur.fetchone()
            return int(row[0]) if row else None

    def put(self, path: str, size: int, mtime_ns: int, tokens: Optional[int]) -> None:
        """Queue ``tokens`` for ``path`` for the next batched commit."""
        if tokens is None:
            tokens = UNREADABLE_SENTINEL
        with self._lock:
            self._pending.append(
                (path, mtime_ns, size, int(tokens), self._encoding_tag)
            )
            if len(self._pending) >= self._batch_size:
                self._commit_locked()

    def commit(self) -> None:
        """Persist queued entries to disk."""
        with self._lock:
            self._commit_locked()

    def close(self) -> None:
        with self._lock:
            self._commit_locked()
            try:
                self._conn.close()
            except Exception as exc:
                logger.debug("Failed to close token cache connection: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _commit_locked(self) -> None:
        if not self._pending:
            return
        entries: Sequence[tuple[str, int, int, int, str]] = tuple(self._pending)
        try:
            self._conn.executemany(
                "INSERT OR REPLACE INTO token_cache (abs_path, mtime_ns, size, tokens, encoding) VALUES (?,?,?,?,?)",
                entries,
            )
        except Exception as exc:
            logger.warning("Token cache insert failed: %s", exc)
            self._pending.clear()
            return
        self._conn.commit()
        self._pending.clear()

    def _atexit_commit(self) -> None:
        try:
            self.commit()
        except Exception as exc:
            logger.debug("Token cache atexit commit failed: %s", exc)


__all__ = ["TokenCache", "DEFAULT_CACHE_PATH", "CACHE_BATCH_SIZE"]
