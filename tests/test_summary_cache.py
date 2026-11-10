import concurrent.futures
from codemerge.llm.config import DEFAULT_GROQ_SUMMARY_MODEL


def test_summary_cache_crud_and_uniqueness(tmp_path):
    from codemerge.summarizer.shared.summary_cache import SummaryCache

    db = tmp_path / "sumcache.db"
    cache = SummaryCache(db)

    try:
        f = tmp_path / "a.py"
        f.write_text("print('a')", encoding="utf-8")
        st = f.stat()
        path = str(f)
        model = DEFAULT_GROQ_SUMMARY_MODEL
        kind = "short"
        fp = "fp123"
        payload = "func (function): p"

        digest = "deadbeef"

        cache.put_ok(
            path,
            mtime=st.st_mtime,
            size=st.st_size,
            model=model,
            kind=kind,
            prompt_fp=fp,
            summary_text=payload,
            sha256=digest,
        )
        row = cache.get(path, model, kind, fp)
        assert row is not None
        assert row["status"] == "ok"
        assert row["summary_text"] == payload
        assert row.get("sha256") == digest

        # Same PK -> update; put_error increments error_count
        cache.put_error(
            path,
            model=model,
            kind=kind,
            prompt_fp=fp,
            mtime=st.st_mtime,
            size=st.st_size,
            sha256=digest,
        )
        row2 = cache.get(path, model, kind, fp)
        assert row2 is not None
        assert row2["status"] == "error"
        assert int(row2["error_count"]) >= 1
        assert row2.get("sha256") == digest

        # bulk_get returns map keyed by abs path
        got = cache.bulk_get([path], model, kind, fp)
        assert path in got
    finally:
        cache.close()


def test_summary_cache_concurrent_put_error(tmp_path):
    from codemerge.summarizer.shared.summary_cache import SummaryCache

    db = tmp_path / "sumcache.db"
    cache = SummaryCache(db)
    try:
        target = tmp_path / "concurrent.py"
        target.write_text("print('x')", encoding="utf-8")
        st = target.stat()

        def worker() -> None:
            cache.put_error(
                str(target),
                model=DEFAULT_GROQ_SUMMARY_MODEL,
                kind="short",
                prompt_fp="fp",
                mtime=st.st_mtime,
                size=st.st_size,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker) for _ in range(6)]
            for fut in futures:
                fut.result(timeout=1)

        row = cache.get(str(target), DEFAULT_GROQ_SUMMARY_MODEL, "short", "fp")
        assert row is not None
        assert int(row["error_count"]) == 6
    finally:
        cache.close()
