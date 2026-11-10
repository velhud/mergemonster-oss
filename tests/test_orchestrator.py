from codemerge.llm.config import DEFAULT_GROQ_SUMMARY_MODEL


def test_ensure_summaries_caches_and_reuses(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import orchestrator as orch
    from codemerge.filemerger import FileProcessor

    a = tmp_path / "a.py"
    a.write_text("print('a')", encoding="utf-8")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
        "summary_prompt_version": 1,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": True,
    }

    calls = {"n": 0}

    def fake_fetch(
        paths,
        file_processor,
        content_mode,
        max_workers=None,
        app_config=None,
        root=None,
        use_batch=False,
        progress_callback=None,
        return_debug=False,
        **_kwargs,
    ):
        calls["n"] += 1
        assert content_mode in ("short_only", "long_only")
        result = ({str(a): "p"}, [])
        return (result[0], result[1], {}) if return_debug else result

    monkeypatch.setattr(orch, "fetch_ai_summaries", fake_fetch)

    fp = FileProcessor()
    # First run fetches
    summaries, errors = orch.ensure_summaries([str(a)], "short", fp, cfg)
    assert errors == []
    assert str(a) in summaries and summaries[str(a)]
    assert calls["n"] == 1

    # Second run uses cache (no new fetch)
    summaries2, errors2 = orch.ensure_summaries([str(a)], "short", fp, cfg)
    assert errors2 == []
    assert str(a) in summaries2 and summaries2[str(a)]
    assert calls["n"] == 1


def test_ensure_summaries_skips_binary(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import orchestrator as orch
    from codemerge.filemerger import FileProcessor

    binary_file = tmp_path / "image.png"
    binary_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
        "summary_prompt_version": 1,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": True,
    }

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("fetch_ai_summaries should not be called for binaries")

    monkeypatch.setattr(orch, "fetch_ai_summaries", fail_fetch)

    fp = FileProcessor()
    summaries, errors = orch.ensure_summaries([str(binary_file)], "short", fp, cfg)
    assert errors == []
    assert summaries[str(binary_file)] is None


def test_ensure_summaries_reloads_stale(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import orchestrator as orch
    from codemerge.filemerger import FileProcessor

    target = tmp_path / "stale.py"
    target.write_text("print('first')\n", encoding="utf-8")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
        "summary_prompt_version": 1,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": True,
    }

    calls = {"n": 0}

    def fake_fetch(paths, *_, return_debug=False, **__):
        calls["n"] += 1
        result = ({paths[0]: f"call {calls['n']}"}, [])
        return (result[0], result[1], {}) if return_debug else result

    monkeypatch.setattr(orch, "fetch_ai_summaries", fake_fetch)

    fp = FileProcessor()
    first, first_errs = orch.ensure_summaries([str(target)], "short", fp, cfg)
    assert first_errs == []
    assert calls["n"] == 1
    assert first[str(target)] == "call 1"

    target.write_text("print('second')\n", encoding="utf-8")

    second, second_errs = orch.ensure_summaries([str(target)], "short", fp, cfg)
    assert second_errs == []
    assert calls["n"] == 2
    assert second[str(target)] == "call 2"


def test_ensure_summaries_backoff_skips_recent_errors(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import orchestrator as orch
    from codemerge.filemerger import FileProcessor
    from codemerge.summarizer.shared.summary_cache import SummaryCache

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
        "summary_prompt_version": 1,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": True,
    }

    target = tmp_path / "error.py"
    target.write_text("print('boom')\n", encoding="utf-8")
    st = target.stat()

    fp_key = orch.file_prompt_fingerprint("short", cfg["summary_prompt_version"], model=cfg["groq_summary_model"])
    cache = SummaryCache(cfg["summary_cache_db_path"])
    try:
        for _ in range(3):
            cache.put_error(
                str(target),
                model=cfg["groq_summary_model"],
                kind="short",
                prompt_fp=fp_key,
                mtime=st.st_mtime,
                size=st.st_size,
            )
    finally:
        cache.close()

    def fail_fetch(*_args, **_kwargs):  # pragma: no cover - should not run
        raise AssertionError("fetch_ai_summaries should be skipped due to backoff")

    monkeypatch.setattr(orch, "fetch_ai_summaries", fail_fetch)

    fp = FileProcessor()
    results, errors = orch.ensure_summaries([str(target)], "short", fp, cfg)
    assert errors == []
    assert results[str(target)] is None


def test_ensure_summaries_skips_binary_by_heuristic(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import orchestrator as orch
    from codemerge.filemerger import FileProcessor

    blob = tmp_path / "blob"
    blob.write_bytes(b"\x00\x01\x02\x03" * 64)

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
        "summary_prompt_version": 1,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": True,
    }

    def fail_fetch(*_args, **_kwargs):  # pragma: no cover - should not run
        raise AssertionError("binary heuristic should prevent fetch")

    monkeypatch.setattr(orch, "fetch_ai_summaries", fail_fetch)

    fp = FileProcessor()
    summaries, errors = orch.ensure_summaries([str(blob)], "short", fp, cfg)
    assert errors == []
    assert summaries[str(blob)] is None


def test_ensure_summaries_strict_long_raises_when_long_summary_is_unavailable(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import orchestrator as orch
    from codemerge.filemerger import FileProcessor
    import pytest

    target = tmp_path / "fallback.py"
    target.write_text("print('fallback')\n", encoding="utf-8")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
        "summary_prompt_version": 1,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": True,
    }

    calls: list[str] = []

    def fake_fetch(
        paths,
        file_processor,
        content_mode,
        max_workers=None,
        app_config=None,
        root=None,
        use_batch=False,
        progress_callback=None,
        return_debug=False,
        **_kwargs,
    ):
        _ = (file_processor, max_workers, app_config, root, use_batch, progress_callback)
        calls.append(content_mode)
        if content_mode == "long_only":
            result = ({}, list(paths))
        else:
            result = ({str(target): "short summary"}, [])
        return (result[0], result[1], {}) if return_debug else result

    monkeypatch.setattr(orch, "fetch_ai_summaries", fake_fetch)

    fp = FileProcessor()
    with pytest.raises(RuntimeError, match="Strict summary required but unavailable"):
        orch.ensure_summaries([str(target)], "long", fp, cfg, strict_required=True)

    assert calls == ["long_only", "long_only"]


def test_ensure_summaries_strict_does_not_keep_stale_cached_summary_when_refresh_fails(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import orchestrator as orch
    from codemerge.summarizer.shared.summary_cache import SummaryCache
    from codemerge.filemerger import FileProcessor
    from codemerge.llm.summarize import file_prompt_fingerprint, resolve_summary_model
    import pytest

    target = tmp_path / "stale.py"
    target.write_text("print('stale')\n", encoding="utf-8")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": DEFAULT_GROQ_SUMMARY_MODEL,
        "summary_prompt_version": 1,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": True,
    }

    fp = FileProcessor()
    model = resolve_summary_model(cfg)
    prompt_fp = file_prompt_fingerprint("long", 1, model=model)
    mtime = target.stat().st_mtime - 10
    size = max(target.stat().st_size - 1, 0)

    cache = SummaryCache(cfg["summary_cache_db_path"])
    try:
        cache.put_ok(
            str(target),
            mtime=mtime,
            size=size,
            model=model,
            kind="long",
            prompt_fp=prompt_fp,
            summary_text="stale long summary",
            sha256=None,
        )
    finally:
        cache.close()

    calls: list[str] = []

    def fake_fetch(
        paths,
        file_processor,
        content_mode,
        max_workers=None,
        app_config=None,
        root=None,
        use_batch=False,
        progress_callback=None,
        return_debug=False,
        **_kwargs,
    ):
        _ = (paths, file_processor, max_workers, app_config, root, use_batch, progress_callback)
        calls.append(content_mode)
        result = ({}, [str(target)])
        return (result[0], result[1], {}) if return_debug else result

    monkeypatch.setattr(orch, "fetch_ai_summaries", fake_fetch)

    with pytest.raises(RuntimeError, match="Strict summary required but unavailable"):
        orch.ensure_summaries([str(target)], "long", fp, cfg, strict_required=True)

    assert calls == ["long_only", "long_only"]
