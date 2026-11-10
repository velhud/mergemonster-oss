from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from codemerge.core.normalize import canonical_path


def _now() -> float:
    return time.time()


def _norm_path(p: str) -> str:
    return canonical_path(p)


def _norm_kind(kind: str) -> str:
    k = str(kind).strip().lower()
    if k not in {"short", "long"}:
        raise ValueError(f"invalid summary kind: {kind!r}")
    return k


_POOL_LOCK = threading.Lock()
_CONNECTION_POOL: dict[str, dict] = {}


def _get_connection(db_path: Path) -> tuple[sqlite3.Connection, threading.Lock, str]:
    thread_id = threading.get_ident()
    key = f"{canonical_path(str(db_path))}::thread:{thread_id}"
    with _POOL_LOCK:
        entry = _CONNECTION_POOL.get(key)
        if entry:
            entry["refcount"] += 1
            return entry["conn"], entry["lock"], key
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        lock = threading.Lock()
        _CONNECTION_POOL[key] = {"conn": conn, "lock": lock, "refcount": 1}
        return conn, lock, key


class SummaryCache:
    """
    SQLite-backed cache of summaries.
    Key: (path, model, kind, prompt_fp).
    Freshness decided outside by change detector using mtime+size (MVP).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._conn, self._lock, self._pool_key = _get_connection(self._path)
        self._prepare_schema()

    # ───────────────────────────── schema ─────────────────────────────
    def _prepare_schema(self) -> None:
        try:
            cur = self._conn.execute("PRAGMA journal_mode=WAL")
            cur.close()
        except Exception:
            pass
        def _write() -> None:
            self._execute_no_result(
                """
                CREATE TABLE IF NOT EXISTS summaries (
                  path        TEXT NOT NULL,
                  mtime       REAL NOT NULL,
                  size        INTEGER NOT NULL,
                  model       TEXT NOT NULL,
                  kind        TEXT NOT NULL,          -- 'short' | 'long'
                  prompt_fp   TEXT NOT NULL,          -- fingerprint of prompt/schema/version
                  status      TEXT NOT NULL,          -- 'ok' | 'error'
                  summary_json TEXT,                  -- plain-text summary payload
                  sha256      TEXT,
                  created_at  REAL NOT NULL,
                  updated_at  REAL NOT NULL,
                  error_count INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY (path, model, kind, prompt_fp)
                )
                """
            )
            self._execute_no_result(
                """
                CREATE INDEX IF NOT EXISTS idx_summaries_fresh
                  ON summaries(path, mtime, size, model, kind, prompt_fp, status)
                """
            )
            self._ensure_column("sha256", "sha256 TEXT")
        self._write_with_retry(_write)

    def _ensure_column(self, name: str, ddl: str) -> None:
        try:
            cur = self._conn.execute("PRAGMA table_info(summaries)")
            try:
                columns = {row[1] for row in cur.fetchall()}
            finally:
                cur.close()
        except Exception:
            columns = set()
        if name in columns:
            return
        try:
            self._execute_no_result(f"ALTER TABLE summaries ADD COLUMN {ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    def _execute_no_result(self, sql: str, params: tuple[object, ...] = ()) -> None:
        cur = self._conn.execute(sql, params)
        cur.close()

    # ───────────────────────────── public API ─────────────────────────
    def close(self) -> None:
        with _POOL_LOCK:
            entry = _CONNECTION_POOL.get(self._pool_key)
            if not entry:
                return
            entry["refcount"] -= 1
            if entry["refcount"] > 0:
                return
            try:
                entry["conn"].commit()
                entry["conn"].close()
            except Exception:
                pass
            _CONNECTION_POOL.pop(self._pool_key, None)

    def _write_with_retry(self, func: callable, *, retries: int = 5) -> None:
        for attempt in range(retries):
            try:
                with self._lock:
                    func()
                    self._conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= retries - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))

    def get(
        self, path: str, model: str, kind: str, prompt_fp: str
    ) -> Optional[Dict]:
        """Return row dict or None."""
        npath = _norm_path(path)
        k = _norm_kind(kind)
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT path, mtime, size, model, kind, prompt_fp,
                       status, summary_json AS summary_text, sha256, created_at, updated_at, error_count
                  FROM summaries
                 WHERE path=? AND model=? AND kind=? AND prompt_fp=?
                """,
                (npath, model, k, prompt_fp),
            )
            try:
                row = cur.fetchone()
            finally:
                cur.close()
        return dict(row) if row else None

    def bulk_get(
        self, paths: Iterable[str], model: str, kind: str, prompt_fp: str
    ) -> Dict[str, Dict]:
        """Return mapping path -> row dict for existing keys."""
        rows: Dict[str, Dict] = {}
        k = _norm_kind(kind)
        batch: list[str] = []
        lookup: Dict[str, str] = {}
        # chunk to avoid very long IN clauses
        for p in paths:
            normalized = _norm_path(p)
            batch.append(normalized)
            lookup.setdefault(normalized, p)
            if len(batch) >= 500:
                self._bulk_fetch_into(rows, batch, lookup, model, k, prompt_fp)
                batch.clear()
        if batch:
            self._bulk_fetch_into(rows, batch, lookup, model, k, prompt_fp)
        return rows

    def put_ok(
        self,
        path: str,
        *,
        mtime: float,
        size: int,
        model: str,
        kind: str,
        prompt_fp: str,
        summary_text: str,
        sha256: str | None = None,
    ) -> None:
        """Upsert status=ok, reset error_count, preserve created_at on conflict."""
        npath = _norm_path(path)
        k = _norm_kind(kind)
        now = _now()
        def _write() -> None:
            self._execute_no_result(
                """
                INSERT INTO summaries
                    (path, mtime, size, model, kind, prompt_fp,
                     status, summary_json, sha256, created_at, updated_at, error_count)
                VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?, 0)
                ON CONFLICT(path, model, kind, prompt_fp) DO UPDATE SET
                    mtime        = excluded.mtime,
                    size         = excluded.size,
                    status       = 'ok',
                    summary_json = excluded.summary_json,
                    sha256       = excluded.sha256,
                    updated_at   = excluded.updated_at,
                    error_count  = 0,
                    created_at   = CASE
                                     WHEN summaries.created_at IS NULL
                                     THEN excluded.created_at
                                     ELSE summaries.created_at
                                   END
                """,
                (
                    npath,
                    float(mtime),
                    int(size),
                    model,
                    k,
                    prompt_fp,
                    summary_text,
                    sha256,
                    now,
                    now,
                ),
            )
        self._write_with_retry(_write)

    def put_error(
        self,
        path: str,
        *,
        model: str,
        kind: str,
        prompt_fp: str,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        sha256: str | None = None,
    ) -> None:
        """
        Upsert status=error and increment error_count.
        If mtime/size unknown, read from filesystem when possible.
        """
        npath = _norm_path(path)
        k = _norm_kind(kind)
        if mtime is None or size is None:
            try:
                st = os.stat(npath)
                mtime = float(st.st_mtime)
                size = int(st.st_size)
            except Exception:
                mtime = float(mtime or 0)
                size = int(size or 0)
        now = _now()
        def _write() -> None:
            self._execute_no_result(
                """
                INSERT INTO summaries
                    (path, mtime, size, model, kind, prompt_fp,
                     status, summary_json, sha256, created_at, updated_at, error_count)
                VALUES (?, ?, ?, ?, ?, ?, 'error', NULL, ?, ?, ?, 1)
                ON CONFLICT(path, model, kind, prompt_fp) DO UPDATE SET
                    mtime        = excluded.mtime,
                    size         = excluded.size,
                    status       = 'error',
                    sha256       = excluded.sha256,
                    updated_at   = excluded.updated_at,
                    error_count  = summaries.error_count + 1
                """,
                (
                    npath,
                    float(mtime),
                    int(size),
                    model,
                    k,
                    prompt_fp,
                    sha256,
                    now,
                    now,
                ),
            )
        self._write_with_retry(_write)

    def invalidate(self, paths: Optional[Iterable[str]] = None) -> None:
        """Delete all rows or only for given paths."""
        def _write() -> None:
            if paths is None:
                self._execute_no_result("DELETE FROM summaries")
            else:
                batch = [_norm_path(p) for p in paths]
                for chunk in _chunks(batch, 500):
                    q = "DELETE FROM summaries WHERE path IN (%s)" % ",".join("?" * len(chunk))
                    self._execute_no_result(q, tuple(chunk))
        self._write_with_retry(_write)

    def purge_stale_prompt_fp(
        self,
        current_fp: str,
        *,
        model: str | None = None,
        kind: str | None = None,
    ) -> int:
        """
        Delete rows with a prompt_fp different from current_fp, optionally scoped by model/kind.
        Returns the number of deleted rows.
        """
        count = 0
        clauses = ["prompt_fp != ?"]
        params: list[object] = [current_fp]
        if model is not None:
            clauses.append("model = ?")
            params.append(str(model))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(_norm_kind(kind))
        sql = "DELETE FROM summaries WHERE " + " AND ".join(clauses)

        def _write() -> None:
            nonlocal count
            cur = self._conn.execute(sql, tuple(params))
            try:
                count = cur.rowcount
            finally:
                cur.close()

        self._write_with_retry(_write)
        return count

    def purge_errors(self) -> int:
        """
        Delete all rows with status='error'.
        Returns the number of deleted rows.
        """
        count = 0
        def _write() -> None:
            nonlocal count
            cur = self._conn.execute("DELETE FROM summaries WHERE status = 'error'")
            try:
                count = cur.rowcount
            finally:
                cur.close()
        self._write_with_retry(_write)
        return count


    # ─────────────────────────── helpers ────────────────────────────
    def _bulk_fetch_into(
        self,
        acc: Dict[str, Dict],
        paths_norm: list[str],
        lookup: Dict[str, str],
        model: str,
        kind_norm: str,
        prompt_fp: str,
    ) -> None:
        placeholders = ",".join("?" * len(paths_norm))
        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT path, mtime, size, model, kind, prompt_fp,
                       status, summary_json AS summary_text, sha256, created_at, updated_at, error_count
                  FROM summaries
                 WHERE model=? AND kind=? AND prompt_fp=? AND path IN ({placeholders})
                """,
                (model, kind_norm, prompt_fp, *paths_norm),
            )
            try:
                fetched = cur.fetchall()
            finally:
                cur.close()
        for row in fetched:
            d = dict(row)
            original = lookup.get(d["path"], d["path"])
            acc[original] = d


def _chunks(seq: Iterable[str], n: int):
    buf: list[str] = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


__all__ = ["SummaryCache"]
