from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Mapping, Optional

from codemerge.core.normalize import canonical_path

def _now() -> float:
    return time.time()


def _norm_path(p: str) -> str:
    return canonical_path(p)


def _norm_kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k not in {"folder", "repo"}:
        raise ValueError(f"invalid aggregate kind: {kind!r}")
    return k


def _norm_mode(mode: str | None) -> str:
    m = str(mode or "full").strip().lower()
    if m not in {"full", "quick"}:
        raise ValueError(f"invalid aggregate mode: {mode!r}")
    return m


_POOL_LOCK = threading.Lock()
_CONNECTION_POOL: dict[str, dict] = {}
_EXPECTED_PK_COLUMNS = ("target_kind", "target_path", "mode", "manifest_hash", "model", "prompt_fp")
_AGGREGATE_COLUMNS = (
    "target_kind",
    "target_path",
    "mode",
    "manifest_hash",
    "inputs_hash",
    "inventory_hash",
    "model",
    "prompt_fp",
    "status",
    "summary_json",
    "file_count",
    "skipped_count",
    "reduction_layers",
    "batch_count",
    "group_counts",
    "inventory_count",
    "scope_label",
    "scope_definition_version",
    "audit_json",
    "created_at",
    "updated_at",
)


def resolve_aggregate_cache_db_path(app_config: Mapping[str, object] | None) -> str | None:
    cfg = app_config or {}
    direct = cfg.get("aggregate_cache_db_path")
    if isinstance(direct, str) and direct.strip():
        return direct
    fallback = cfg.get("summary_cache_db_path")
    if isinstance(fallback, str) and fallback.strip():
        return fallback
    return None


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


class AggregateCache:
    """SQLite-backed cache for folder/repo summaries."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._conn, self._lock, self._pool_key = _get_connection(self._path)
        self._prepare_schema()

    def _prepare_schema(self) -> None:
        try:
            cur = self._conn.execute("PRAGMA journal_mode=WAL")
            cur.close()
        except Exception:
            pass
        self._execute_no_result(self._create_table_sql())
        self._ensure_column("mode", "mode TEXT NOT NULL DEFAULT 'full'")
        self._ensure_column("file_count", "file_count INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("skipped_count", "skipped_count INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("reduction_layers", "reduction_layers INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("batch_count", "batch_count INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("group_counts", "group_counts TEXT")
        self._ensure_column("inputs_hash", "inputs_hash TEXT")
        self._ensure_column("inventory_hash", "inventory_hash TEXT")
        self._ensure_column("inventory_count", "inventory_count INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("scope_label", "scope_label TEXT")
        self._ensure_column("scope_definition_version", "scope_definition_version INTEGER")
        self._ensure_column("audit_json", "audit_json TEXT")
        self._migrate_primary_key_if_needed()
        self._execute_no_result(
            """
            CREATE INDEX IF NOT EXISTS idx_aggregates_manifest
              ON aggregates(target_kind, target_path, mode, manifest_hash, status)
            """
        )
        self._conn.commit()

    def _create_table_sql(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS aggregates (
              target_kind   TEXT NOT NULL,
              target_path   TEXT NOT NULL,
              mode          TEXT NOT NULL DEFAULT 'full',
              manifest_hash TEXT NOT NULL,
              inputs_hash   TEXT,
              inventory_hash TEXT,
              model         TEXT NOT NULL,
              prompt_fp     TEXT NOT NULL,
              status        TEXT NOT NULL,
              summary_json  TEXT,
              file_count    INTEGER NOT NULL DEFAULT 0,
              skipped_count INTEGER NOT NULL DEFAULT 0,
              reduction_layers INTEGER NOT NULL DEFAULT 0,
              batch_count   INTEGER NOT NULL DEFAULT 0,
              group_counts  TEXT,
              inventory_count INTEGER NOT NULL DEFAULT 0,
              scope_label   TEXT,
              scope_definition_version INTEGER,
              audit_json    TEXT,
              created_at    REAL NOT NULL,
              updated_at    REAL NOT NULL,
              PRIMARY KEY (target_kind, target_path, mode, manifest_hash, model, prompt_fp)
            )
        """

    def _table_columns(self, table_name: str) -> set[str]:
        try:
            cur = self._conn.execute(f"PRAGMA table_info({table_name})")
            try:
                return {str(row[1]) for row in cur.fetchall()}
            finally:
                cur.close()
        except Exception:
            return set()

    def _primary_key_columns(self) -> list[str]:
        try:
            cur = self._conn.execute("PRAGMA table_info(aggregates)")
            try:
                rows = list(cur.fetchall())
            finally:
                cur.close()
        except Exception:
            return []
        keyed = [row for row in rows if int(row[5] or 0) > 0]
        keyed.sort(key=lambda row: int(row[5]))
        return [str(row[1]) for row in keyed]

    def _migration_select_expr(self, column: str, *, legacy_columns: set[str]) -> str:
        if column in legacy_columns:
            return column
        defaults = {
            "mode": "'full'",
            "inputs_hash": "NULL",
            "inventory_hash": "NULL",
            "summary_json": "NULL",
            "group_counts": "NULL",
            "inventory_count": "0",
            "scope_label": "NULL",
            "scope_definition_version": "NULL",
            "audit_json": "NULL",
        }
        return defaults.get(column, "NULL")

    def _migrate_primary_key_if_needed(self) -> None:
        current_pk = tuple(self._primary_key_columns())
        if current_pk == _EXPECTED_PK_COLUMNS:
            return
        legacy_name = f"aggregates_legacy_{int(time.time() * 1000)}"
        self._execute_no_result(f"ALTER TABLE aggregates RENAME TO {legacy_name}")
        self._execute_no_result(self._create_table_sql())
        legacy_columns = self._table_columns(legacy_name)
        target_columns = [column for column in _AGGREGATE_COLUMNS]
        select_columns = [
            f"{self._migration_select_expr(column, legacy_columns=legacy_columns)} AS {column}"
            for column in target_columns
        ]
        self._execute_no_result(
            f"""
            INSERT OR REPLACE INTO aggregates ({", ".join(target_columns)})
            SELECT {", ".join(select_columns)}
              FROM {legacy_name}
             ORDER BY updated_at ASC
            """
        )
        self._execute_no_result(f"DROP TABLE {legacy_name}")

    def _ensure_column(self, name: str, ddl: str) -> None:
        try:
            cur = self._conn.execute("PRAGMA table_info(aggregates)")
            try:
                columns = {row[1] for row in cur.fetchall()}
            finally:
                cur.close()
        except Exception:
            columns = set()
        if name in columns:
            return
        try:
            self._execute_no_result(f"ALTER TABLE aggregates ADD COLUMN {ddl}")
        except Exception:
            pass

    def _execute_no_result(self, sql: str, params: tuple[object, ...] = ()) -> None:
        cur = self._conn.execute(sql, params)
        cur.close()

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
        self,
        kind: str,
        path: str,
        model: str,
        prompt_fp: str,
        *,
        mode: str | None = None,
        manifest_hash: str | None = None,
        inventory_hash: str | None = None,
        inputs_hash: str | None = None,
    ) -> Optional[Dict]:
        npath = _norm_path(path)
        k = _norm_kind(kind)
        m = _norm_mode(mode) if mode is not None else None
        clauses = ["target_kind=?", "target_path=?", "model=?", "prompt_fp=?"]
        params: list[object] = [k, npath, model, prompt_fp]
        if m is not None:
            clauses.append("mode=?")
            params.append(m)
        if isinstance(manifest_hash, str) and manifest_hash:
            clauses.append("manifest_hash=?")
            params.append(manifest_hash)
        if isinstance(inventory_hash, str) and inventory_hash:
            clauses.append("inventory_hash=?")
            params.append(inventory_hash)
        if isinstance(inputs_hash, str) and inputs_hash:
            clauses.append("inputs_hash=?")
            params.append(inputs_hash)
        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT target_kind, target_path, mode, manifest_hash, inputs_hash, inventory_hash,
                       model, prompt_fp, status, summary_json AS summary_text,
                       file_count, skipped_count, reduction_layers, batch_count, group_counts,
                       inventory_count, scope_label, scope_definition_version,
                       audit_json,
                       created_at, updated_at
                  FROM aggregates
                 WHERE {" AND ".join(clauses)}
                 ORDER BY updated_at DESC
                 LIMIT 1
                """,
                tuple(params),
            )
            try:
                row = cur.fetchone()
            finally:
                cur.close()
        return dict(row) if row else None

    def put_ok(
        self,
        kind: str,
        path: str,
        *,
        mode: str = "full",
        manifest_hash: str,
        inputs_hash: str | None = None,
        inventory_hash: str | None = None,
        model: str,
        prompt_fp: str,
        summary_text: str,
        file_count: int,
        skipped_count: int,
        reduction_layers: int,
        batch_count: int,
        group_counts: str | None = None,
        inventory_count: int = 0,
        scope_label: str | None = None,
        scope_definition_version: int | None = None,
        audit_json: str | None = None,
    ) -> None:
        npath = _norm_path(path)
        k = _norm_kind(kind)
        m = _norm_mode(mode)
        now = _now()
        def _write() -> None:
            self._execute_no_result(
                """
                INSERT INTO aggregates
                    (target_kind, target_path, mode, manifest_hash, inputs_hash, inventory_hash,
                     model, prompt_fp, status, summary_json, file_count, skipped_count,
                     reduction_layers, batch_count, group_counts, inventory_count,
                     scope_label, scope_definition_version, audit_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_kind, target_path, mode, manifest_hash, model, prompt_fp) DO UPDATE SET
                    manifest_hash = excluded.manifest_hash,
                    inputs_hash   = excluded.inputs_hash,
                    inventory_hash = excluded.inventory_hash,
                    mode          = excluded.mode,
                    status        = 'ok',
                    summary_json  = excluded.summary_json,
                    file_count    = excluded.file_count,
                    skipped_count = excluded.skipped_count,
                    reduction_layers = excluded.reduction_layers,
                    batch_count   = excluded.batch_count,
                    group_counts  = excluded.group_counts,
                    inventory_count = excluded.inventory_count,
                    scope_label   = excluded.scope_label,
                    scope_definition_version = excluded.scope_definition_version,
                    audit_json    = excluded.audit_json,
                    updated_at    = excluded.updated_at,
                    created_at    = CASE
                                      WHEN aggregates.created_at IS NULL
                                      THEN excluded.created_at
                                      ELSE aggregates.created_at
                                    END
                """,
                (
                    k,
                    npath,
                    m,
                    manifest_hash,
                    inputs_hash,
                    inventory_hash,
                    model,
                    prompt_fp,
                    summary_text,
                    int(file_count),
                    int(skipped_count),
                    int(reduction_layers),
                    int(batch_count),
                    group_counts,
                    int(inventory_count),
                    scope_label,
                    scope_definition_version,
                    audit_json,
                    now,
                    now,
                ),
            )
        self._write_with_retry(_write)

    def put_error(
        self,
        kind: str,
        path: str,
        *,
        mode: str = "full",
        manifest_hash: str,
        inputs_hash: str | None = None,
        inventory_hash: str | None = None,
        model: str,
        prompt_fp: str,
        file_count: int = 0,
        skipped_count: int = 0,
        reduction_layers: int = 0,
        batch_count: int = 0,
        group_counts: str | None = None,
        inventory_count: int = 0,
        scope_label: str | None = None,
        scope_definition_version: int | None = None,
        audit_json: str | None = None,
    ) -> None:
        npath = _norm_path(path)
        k = _norm_kind(kind)
        m = _norm_mode(mode)
        now = _now()
        def _write() -> None:
            cur = self._conn.execute(
                """
                SELECT status, summary_json
                  FROM aggregates
                 WHERE target_kind=? AND target_path=? AND mode=? AND manifest_hash=? AND model=? AND prompt_fp=?
                """,
                (k, npath, m, manifest_hash, model, prompt_fp),
            )
            try:
                existing = cur.fetchone()
            finally:
                cur.close()
            if (
                existing
                and str(existing["status"] or "").strip().lower() == "ok"
                and isinstance(existing["summary_json"], str)
                and existing["summary_json"].strip()
            ):
                return
            self._execute_no_result(
                """
                INSERT INTO aggregates
                    (target_kind, target_path, mode, manifest_hash, inputs_hash, inventory_hash,
                     model, prompt_fp, status, summary_json, file_count, skipped_count,
                     reduction_layers, batch_count, group_counts, inventory_count,
                     scope_label, scope_definition_version, audit_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'error', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_kind, target_path, mode, manifest_hash, model, prompt_fp) DO UPDATE SET
                    manifest_hash = excluded.manifest_hash,
                    inputs_hash   = excluded.inputs_hash,
                    inventory_hash = excluded.inventory_hash,
                    mode          = excluded.mode,
                    status        = 'error',
                    summary_json  = NULL,
                    file_count    = excluded.file_count,
                    skipped_count = excluded.skipped_count,
                    reduction_layers = excluded.reduction_layers,
                    batch_count   = excluded.batch_count,
                    group_counts  = excluded.group_counts,
                    inventory_count = excluded.inventory_count,
                    scope_label   = excluded.scope_label,
                    scope_definition_version = excluded.scope_definition_version,
                    audit_json    = excluded.audit_json,
                    updated_at    = excluded.updated_at,
                    created_at    = CASE
                                      WHEN aggregates.created_at IS NULL
                                      THEN excluded.created_at
                                      ELSE aggregates.created_at
                                    END
                """,
                (
                    k,
                    npath,
                    m,
                    manifest_hash,
                    inputs_hash,
                    inventory_hash,
                    model,
                    prompt_fp,
                    int(file_count),
                    int(skipped_count),
                    int(reduction_layers),
                    int(batch_count),
                    group_counts,
                    int(inventory_count),
                    scope_label,
                    scope_definition_version,
                    audit_json,
                    now,
                    now,
                ),
            )
        self._write_with_retry(_write)

    def purge_stale_prompt_fp(
        self,
        current_fp: str,
        *,
        target_kind: str | None = None,
        model: str | None = None,
    ) -> int:
        """
        Delete rows with a prompt_fp different from current_fp, optionally scoped by kind/model.
        Returns the number of deleted rows.
        """
        count = 0
        clauses = ["prompt_fp != ?"]
        params: list[object] = [current_fp]
        if target_kind is not None:
            clauses.append("target_kind = ?")
            params.append(_norm_kind(target_kind))
        if model is not None:
            clauses.append("model = ?")
            params.append(str(model))
        sql = "DELETE FROM aggregates WHERE " + " AND ".join(clauses)

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
            cur = self._conn.execute("DELETE FROM aggregates WHERE status = 'error'")
            try:
                count = cur.rowcount
            finally:
                cur.close()
        self._write_with_retry(_write)
        return count

    def invalidate(
        self,
        *,
        kind: str | None = None,
        path: str | None = None,
        mode: str | None = None,
    ) -> None:
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("target_kind = ?")
            params.append(_norm_kind(kind))
        if path is not None:
            clauses.append("target_path = ?")
            params.append(_norm_path(path))
        if mode is not None:
            clauses.append("mode = ?")
            params.append(_norm_mode(mode))
        sql = "DELETE FROM aggregates"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        def _write() -> None:
            self._execute_no_result(sql, tuple(params))

        self._write_with_retry(_write)

__all__ = ["AggregateCache"]
