import tempfile
import sqlite3
import threading
import unittest
from pathlib import Path

from codemerge.core.normalize import canonical_path
from codemerge.summarizer.shared.aggregate_cache import AggregateCache
from codemerge.summarizer.shared.evidence import compute_inputs_hash, compute_inventory_hash
from codemerge.summarizer.shared.summary_cache import SummaryCache


class CacheScopeTests(unittest.TestCase):
    def test_summary_cache_purge_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = SummaryCache(Path(td) / "summary.db")
            try:
                cache.put_ok(
                    "a.py",
                    mtime=1.0,
                    size=10,
                    model="m1",
                    kind="short",
                    prompt_fp="fp1",
                    summary_text="summary",
                )
                cache.put_ok(
                    "b.py",
                    mtime=1.0,
                    size=10,
                    model="m1",
                    kind="short",
                    prompt_fp="fp2",
                    summary_text="summary",
                )
                cache.put_ok(
                    "c.py",
                    mtime=1.0,
                    size=10,
                    model="m1",
                    kind="long",
                    prompt_fp="fp3",
                    summary_text="summary",
                )
                cache.put_ok(
                    "d.py",
                    mtime=1.0,
                    size=10,
                    model="m2",
                    kind="short",
                    prompt_fp="fp4",
                    summary_text="summary",
                )
                deleted = cache.purge_stale_prompt_fp("fp1", model="m1", kind="short")
                self.assertEqual(deleted, 1)
                cur = cache._conn.execute(
                    "SELECT path, model, kind, prompt_fp FROM summaries"
                )
                rows = {
                    (row["path"], row["model"], row["kind"], row["prompt_fp"])
                    for row in cur.fetchall()
                }
                self.assertIn((canonical_path("a.py"), "m1", "short", "fp1"), rows)
                self.assertIn((canonical_path("c.py"), "m1", "long", "fp3"), rows)
                self.assertIn((canonical_path("d.py"), "m2", "short", "fp4"), rows)
                self.assertNotIn((canonical_path("b.py"), "m1", "short", "fp2"), rows)
            finally:
                cache.close()

    def test_aggregate_cache_purge_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                root = Path(td)
                folder_a = root / "folder-a"
                folder_b = root / "folder-b"
                repo_path = root
                cache.put_ok(
                    "folder",
                    str(folder_a),
                    manifest_hash="h1",
                    model="m1",
                    prompt_fp="fp-folder",
                    summary_text="summary",
                    file_count=0,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                )
                cache.put_ok(
                    "folder",
                    str(folder_b),
                    manifest_hash="h2",
                    model="m1",
                    prompt_fp="fp-old",
                    summary_text="summary",
                    file_count=0,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                )
                cache.put_ok(
                    "repo",
                    str(repo_path),
                    manifest_hash="h3",
                    model="m1",
                    prompt_fp="fp-repo",
                    summary_text="summary",
                    file_count=0,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                )
                deleted = cache.purge_stale_prompt_fp(
                    "fp-folder",
                    target_kind="folder",
                    model="m1",
                )
                self.assertEqual(deleted, 1)
                cur = cache._conn.execute(
                    "SELECT target_kind, target_path, prompt_fp FROM aggregates"
                )
                rows = {
                    (row["target_kind"], row["target_path"], row["prompt_fp"])
                    for row in cur.fetchall()
                }
                self.assertIn(
                    ("folder", canonical_path(str(folder_a)), "fp-folder"),
                    rows,
                )
                self.assertIn(
                    ("repo", canonical_path(str(repo_path)), "fp-repo"),
                    rows,
                )
                self.assertNotIn(
                    ("folder", canonical_path(str(folder_b)), "fp-old"),
                    rows,
                )
            finally:
                cache.close()

    def test_quick_aggregate_cache_tracks_inputs_hash_separately_from_inventory_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                repo_path = Path(td) / "repo"
                repo_path.mkdir()

                inventory_v1 = ["src/a.py", "src/b.py"]
                evidence_v1 = [("src/a.py", "alpha"), ("src/b.py", "beta")]
                evidence_v2 = [("src/a.py", "alpha updated"), ("src/b.py", "beta")]

                inventory_hash_v1 = compute_inventory_hash(inventory_v1)
                inventory_hash_v2 = compute_inventory_hash([path for path, _text in evidence_v2])
                inputs_hash_v1 = compute_inputs_hash(evidence_v1)
                inputs_hash_v2 = compute_inputs_hash(evidence_v2)

                self.assertEqual(inventory_hash_v1, inventory_hash_v2)
                self.assertNotEqual(inputs_hash_v1, inputs_hash_v2)

                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="quick",
                    manifest_hash=inventory_hash_v1,
                    inputs_hash=inputs_hash_v1,
                    inventory_hash=inventory_hash_v1,
                    model="m1",
                    prompt_fp="fp-quick",
                    summary_text="summary v1",
                    file_count=2,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    group_counts=None,
                    inventory_count=2,
                    scope_label="text_only_excluding_binary_assets",
                    scope_definition_version=1,
                )
                row1 = cache.get("repo", str(repo_path), "m1", "fp-quick", mode="quick")
                self.assertIsNotNone(row1)
                self.assertEqual(row1["inventory_hash"], inventory_hash_v1)
                self.assertEqual(row1["inputs_hash"], inputs_hash_v1)

                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="quick",
                    manifest_hash=inventory_hash_v2,
                    inputs_hash=inputs_hash_v2,
                    inventory_hash=inventory_hash_v2,
                    model="m1",
                    prompt_fp="fp-quick",
                    summary_text="summary v2",
                    file_count=2,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    group_counts=None,
                    inventory_count=2,
                    scope_label="text_only_excluding_binary_assets",
                    scope_definition_version=1,
                )
                row2 = cache.get("repo", str(repo_path), "m1", "fp-quick", mode="quick")
                self.assertIsNotNone(row2)
                self.assertEqual(row2["inventory_hash"], inventory_hash_v2)
                self.assertEqual(row2["inputs_hash"], inputs_hash_v2)
            finally:
                cache.close()

    def test_aggregate_cache_keeps_distinct_manifest_rows_for_same_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                repo_path = Path(td) / "repo"
                repo_path.mkdir()
                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-a",
                    inputs_hash="inputs-a",
                    inventory_hash="inventory-a",
                    model="m1",
                    prompt_fp="fp-full",
                    summary_text="summary a",
                    file_count=1,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    inventory_count=1,
                )
                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-b",
                    inputs_hash="inputs-b",
                    inventory_hash="inventory-b",
                    model="m1",
                    prompt_fp="fp-full",
                    summary_text="summary b",
                    file_count=2,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    inventory_count=2,
                )

                row_a = cache.get(
                    "repo",
                    str(repo_path),
                    "m1",
                    "fp-full",
                    mode="full",
                    manifest_hash="manifest-a",
                )
                row_b = cache.get(
                    "repo",
                    str(repo_path),
                    "m1",
                    "fp-full",
                    mode="full",
                    manifest_hash="manifest-b",
                )

                self.assertIsNotNone(row_a)
                self.assertIsNotNone(row_b)
                self.assertEqual(row_a["summary_text"], "summary a")
                self.assertEqual(row_b["summary_text"], "summary b")
            finally:
                cache.close()

    def test_aggregate_cache_put_error_preserves_last_ok_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                repo_path = Path(td) / "repo"
                repo_path.mkdir()
                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-ok",
                    model="m1",
                    prompt_fp="fp-full",
                    summary_text="trusted summary",
                    file_count=2,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    audit_json='{"status":"ok"}',
                )

                cache.put_error(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-error",
                    model="m1",
                    prompt_fp="fp-full",
                    file_count=2,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    audit_json='{"status":"error"}',
                )

                ok_row = cache.get(
                    "repo",
                    str(repo_path),
                    "m1",
                    "fp-full",
                    mode="full",
                    manifest_hash="manifest-ok",
                )
                error_row = cache.get(
                    "repo",
                    str(repo_path),
                    "m1",
                    "fp-full",
                    mode="full",
                    manifest_hash="manifest-error",
                )
                self.assertIsNotNone(ok_row)
                self.assertIsNotNone(error_row)
                self.assertEqual(ok_row["status"], "ok")
                self.assertEqual(ok_row["summary_text"], "trusted summary")
                self.assertEqual(ok_row["manifest_hash"], "manifest-ok")
                self.assertEqual(error_row["status"], "error")
            finally:
                cache.close()

    def test_summary_cache_reads_hold_connection_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = SummaryCache(Path(td) / "summary.db")
            try:
                observed = {"get_locked": False, "bulk_locked": False}
                original_execute = cache._conn.execute

                class ProxyConn:
                    def __init__(self, inner):
                        self._inner = inner

                    def execute(self, sql, params=()):
                        text = str(sql)
                        if "FROM summaries" in text:
                            if "path IN" in text:
                                observed["bulk_locked"] = cache._lock.locked()
                            else:
                                observed["get_locked"] = cache._lock.locked()
                        return original_execute(sql, params)

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                cache._conn = ProxyConn(cache._conn)  # type: ignore[assignment]
                cache.get("missing.py", "m1", "short", "fp1")
                cache.bulk_get(["missing.py"], "m1", "short", "fp1")

                self.assertTrue(observed["get_locked"])
                self.assertTrue(observed["bulk_locked"])
            finally:
                cache.close()

    def test_aggregate_cache_reads_hold_connection_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                observed = {"locked": False}
                original_execute = cache._conn.execute

                class ProxyConn:
                    def __init__(self, inner):
                        self._inner = inner

                    def execute(self, sql, params=()):
                        text = str(sql)
                        if "FROM aggregates" in text:
                            observed["locked"] = cache._lock.locked()
                        return original_execute(sql, params)

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                cache._conn = ProxyConn(cache._conn)  # type: ignore[assignment]
                cache.get("repo", str(Path(td) / "repo"), "m1", "fp1", mode="full")

                self.assertTrue(observed["locked"])
            finally:
                cache.close()

    def test_aggregate_cache_put_error_closes_lookup_cursor_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                repo_path = Path(td) / "repo"
                repo_path.mkdir()
                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-ok",
                    inputs_hash="inputs-ok",
                    inventory_hash="inventory-ok",
                    model="m1",
                    prompt_fp="fp-full",
                    summary_text="trusted summary",
                    file_count=1,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    inventory_count=1,
                    audit_json='{"status":"ok"}',
                )

                tracker = {"open_read": False}
                original_conn = cache._conn

                class ProxyCursor:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def fetchone(self):
                        return self._inner.fetchone()

                    def fetchall(self):
                        return self._inner.fetchall()

                    def close(self):
                        self._tracker["open_read"] = False
                        return self._inner.close()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                class ProxyConn:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def execute(self, sql, params=()):
                        cursor = self._inner.execute(sql, params)
                        if "SELECT status, summary_json" in str(sql):
                            self._tracker["open_read"] = True
                            return ProxyCursor(cursor, self._tracker)
                        return cursor

                    def commit(self):
                        if self._tracker["open_read"]:
                            raise sqlite3.OperationalError("cannot commit transaction - SQL statements in progress")
                        return self._inner.commit()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                cache._conn = ProxyConn(original_conn, tracker)  # type: ignore[assignment]
                cache.put_error(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-error",
                    inputs_hash="inputs-error",
                    inventory_hash="inventory-error",
                    model="m1",
                    prompt_fp="fp-full",
                    audit_json='{"status":"error"}',
                )

                row = cache.get(
                    "repo",
                    str(repo_path),
                    "m1",
                    "fp-full",
                    mode="full",
                    manifest_hash="manifest-ok",
                )
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "ok")
                self.assertEqual(row["manifest_hash"], "manifest-ok")
            finally:
                cache.close()

    def test_aggregate_cache_put_ok_closes_write_cursor_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                tracker = {"open_write": False}
                original_conn = cache._conn

                class ProxyCursor:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def close(self):
                        self._tracker["open_write"] = False
                        return self._inner.close()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                class ProxyConn:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def execute(self, sql, params=()):
                        cursor = self._inner.execute(sql, params)
                        if "INSERT INTO aggregates" in str(sql):
                            self._tracker["open_write"] = True
                            return ProxyCursor(cursor, self._tracker)
                        return cursor

                    def commit(self):
                        if self._tracker["open_write"]:
                            raise sqlite3.OperationalError("cannot commit transaction - SQL statements in progress")
                        return self._inner.commit()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                cache._conn = ProxyConn(original_conn, tracker)  # type: ignore[assignment]
                repo_path = Path(td) / "repo"
                repo_path.mkdir()
                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-ok",
                    inputs_hash="inputs-ok",
                    inventory_hash="inventory-ok",
                    model="m1",
                    prompt_fp="fp-full",
                    summary_text="trusted summary",
                    file_count=1,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                    inventory_count=1,
                    audit_json='{"status":"ok"}',
                )

                row = cache.get("repo", str(repo_path), "m1", "fp-full", mode="full")
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "ok")
            finally:
                cache.close()

    def test_summary_cache_put_ok_closes_write_cursor_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = SummaryCache(Path(td) / "summary.db")
            try:
                tracker = {"open_write": False}
                original_conn = cache._conn

                class ProxyCursor:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def close(self):
                        self._tracker["open_write"] = False
                        return self._inner.close()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                class ProxyConn:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def execute(self, sql, params=()):
                        cursor = self._inner.execute(sql, params)
                        if "INSERT INTO summaries" in str(sql):
                            self._tracker["open_write"] = True
                            return ProxyCursor(cursor, self._tracker)
                        return cursor

                    def commit(self):
                        if self._tracker["open_write"]:
                            raise sqlite3.OperationalError("cannot commit transaction - SQL statements in progress")
                        return self._inner.commit()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                cache._conn = ProxyConn(original_conn, tracker)  # type: ignore[assignment]
                path = Path(td) / "sample.py"
                path.write_text("print('ok')\n", encoding="utf-8")
                cache.put_ok(
                    str(path),
                    mtime=1.0,
                    size=12,
                    model="m1",
                    kind="short",
                    prompt_fp="fp-short",
                    summary_text="summary",
                )

                row = cache.get(str(path), "m1", "short", "fp-short")
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "ok")
            finally:
                cache.close()

    def test_summary_cache_purge_stale_prompt_fp_closes_delete_cursor_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = SummaryCache(Path(td) / "summary.db")
            try:
                cache.put_ok(
                    "a.py",
                    mtime=1.0,
                    size=10,
                    model="m1",
                    kind="short",
                    prompt_fp="fp1",
                    summary_text="summary",
                )
                cache.put_ok(
                    "b.py",
                    mtime=1.0,
                    size=10,
                    model="m1",
                    kind="short",
                    prompt_fp="fp2",
                    summary_text="summary",
                )

                tracker = {"open_delete": False}
                original_conn = cache._conn

                class ProxyCursor:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def close(self):
                        self._tracker["open_delete"] = False
                        return self._inner.close()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                class ProxyConn:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def execute(self, sql, params=()):
                        cursor = self._inner.execute(sql, params)
                        if str(sql).startswith("DELETE FROM summaries"):
                            self._tracker["open_delete"] = True
                            return ProxyCursor(cursor, self._tracker)
                        return cursor

                    def commit(self):
                        if self._tracker["open_delete"]:
                            raise sqlite3.OperationalError("cannot commit transaction - SQL statements in progress")
                        return self._inner.commit()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                cache._conn = ProxyConn(original_conn, tracker)  # type: ignore[assignment]
                deleted = cache.purge_stale_prompt_fp("fp1", model="m1", kind="short")
                self.assertEqual(deleted, 1)
            finally:
                cache.close()

    def test_aggregate_cache_purge_stale_prompt_fp_closes_delete_cursor_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache = AggregateCache(Path(td) / "aggregate.db")
            try:
                repo_path = Path(td) / "repo"
                repo_path.mkdir()
                cache.put_ok(
                    "repo",
                    str(repo_path),
                    mode="full",
                    manifest_hash="manifest-ok",
                    model="m1",
                    prompt_fp="fp1",
                    summary_text="summary",
                    file_count=1,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                )
                cache.put_ok(
                    "folder",
                    str(Path(td) / "folder"),
                    mode="full",
                    manifest_hash="manifest-old",
                    model="m1",
                    prompt_fp="fp-old",
                    summary_text="summary",
                    file_count=1,
                    skipped_count=0,
                    reduction_layers=0,
                    batch_count=0,
                )

                tracker = {"open_delete": False}
                original_conn = cache._conn

                class ProxyCursor:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def close(self):
                        self._tracker["open_delete"] = False
                        return self._inner.close()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                class ProxyConn:
                    def __init__(self, inner, tracker_ref):
                        self._inner = inner
                        self._tracker = tracker_ref

                    def execute(self, sql, params=()):
                        cursor = self._inner.execute(sql, params)
                        if str(sql).startswith("DELETE FROM aggregates"):
                            self._tracker["open_delete"] = True
                            return ProxyCursor(cursor, self._tracker)
                        return cursor

                    def commit(self):
                        if self._tracker["open_delete"]:
                            raise sqlite3.OperationalError("cannot commit transaction - SQL statements in progress")
                        return self._inner.commit()

                    def __getattr__(self, name):
                        return getattr(self._inner, name)

                cache._conn = ProxyConn(original_conn, tracker)  # type: ignore[assignment]
                deleted = cache.purge_stale_prompt_fp("fp1", target_kind="repo", model="m1")
                self.assertEqual(deleted, 0)
            finally:
                cache.close()

    def test_summary_cache_uses_thread_local_connections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "summary.db"
            main_cache = SummaryCache(db_path)
            worker_info: dict[str, object] = {}

            def _worker() -> None:
                worker_a = SummaryCache(db_path)
                worker_b = SummaryCache(db_path)
                try:
                    worker_info["conn_ids"] = (id(worker_a._conn), id(worker_b._conn))
                    worker_info["pool_keys"] = (worker_a._pool_key, worker_b._pool_key)
                finally:
                    worker_b.close()
                    worker_a.close()

            thread = threading.Thread(target=_worker)
            thread.start()
            thread.join()
            try:
                worker_conn_ids = worker_info["conn_ids"]
                worker_pool_keys = worker_info["pool_keys"]
                self.assertEqual(worker_conn_ids[0], worker_conn_ids[1])
                self.assertEqual(worker_pool_keys[0], worker_pool_keys[1])
                self.assertNotEqual(id(main_cache._conn), worker_conn_ids[0])
                self.assertNotEqual(main_cache._pool_key, worker_pool_keys[0])
            finally:
                main_cache.close()

    def test_aggregate_cache_uses_thread_local_connections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "aggregate.db"
            main_cache = AggregateCache(db_path)
            worker_info: dict[str, object] = {}

            def _worker() -> None:
                worker_a = AggregateCache(db_path)
                worker_b = AggregateCache(db_path)
                try:
                    worker_info["conn_ids"] = (id(worker_a._conn), id(worker_b._conn))
                    worker_info["pool_keys"] = (worker_a._pool_key, worker_b._pool_key)
                finally:
                    worker_b.close()
                    worker_a.close()

            thread = threading.Thread(target=_worker)
            thread.start()
            thread.join()
            try:
                worker_conn_ids = worker_info["conn_ids"]
                worker_pool_keys = worker_info["pool_keys"]
                self.assertEqual(worker_conn_ids[0], worker_conn_ids[1])
                self.assertEqual(worker_pool_keys[0], worker_pool_keys[1])
                self.assertNotEqual(id(main_cache._conn), worker_conn_ids[0])
                self.assertNotEqual(main_cache._pool_key, worker_pool_keys[0])
            finally:
                main_cache.close()
