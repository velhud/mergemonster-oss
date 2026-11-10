def test_is_stale_mtime_size(tmp_path):
    from codemerge.summarizer.shared import change_detect as cd

    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    st = f.stat()
    cached = {"mtime": st.st_mtime, "size": st.st_size, "status": "ok"}

    assert cd.is_stale(str(f), cached, strategy="mtime_size") is False

    # Change size -> stale
    f.write_text("hello world", encoding="utf-8")
    assert cd.is_stale(str(f), cached, strategy="mtime_size") is True


def test_bulk_partition(tmp_path):
    from codemerge.summarizer.shared import change_detect as cd

    a = tmp_path / "a.py"
    a.write_text("a", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("bb", encoding="utf-8")
    ca = {"mtime": a.stat().st_mtime, "size": a.stat().st_size, "status": "ok"}
    fresh, stale, missing = cd.bulk_partition([str(a), str(b)], {str(a): ca}, strategy="mtime_size")
    assert str(a) in fresh and str(b) in missing


def test_is_stale_sha256(tmp_path):
    import os
    from codemerge.summarizer.shared import change_detect as cd

    f = tmp_path / "a.txt"
    f.write_text("aaaaa", encoding="utf-8")
    st = f.stat()
    digest = cd.file_sha256(str(f))
    cached = {
        "mtime": st.st_mtime,
        "size": st.st_size,
        "status": "ok",
        "sha256": digest,
    }

    assert cd.is_stale(str(f), cached, strategy="sha256") is False

    # Change content without altering size or persisted mtime
    f.write_text("bbbbb", encoding="utf-8")
    os.utime(f, (st.st_atime, st.st_mtime))

    assert cd.is_stale(str(f), cached, strategy="sha256") is True


def test_is_stale_git_strategy(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import change_detect as cd

    f = tmp_path / "tracked.py"
    f.write_text("print('hello')\n", encoding="utf-8")
    st = f.stat()
    row = {
        "mtime": st.st_mtime,
        "size": st.st_size,
        "status": "ok",
    }

    ctx = cd.GitCtx(str(tmp_path))

    monkeypatch.setattr(cd.GitCtx, "changed_abs_paths", lambda self: {str(f.resolve())})
    assert cd.is_stale(str(f), row, strategy="git", git_ctx=ctx) is True

    monkeypatch.setattr(cd.GitCtx, "changed_abs_paths", lambda self: set())
    assert cd.is_stale(str(f), row, strategy="git", git_ctx=ctx) is False


def test_bulk_partition_git_ctx_cached(monkeypatch, tmp_path):
    from codemerge.summarizer.shared import change_detect as cd

    f = tmp_path / "tracked.py"
    f.write_text("print('hello')\n", encoding="utf-8")
    st = f.stat()
    row = {"mtime": st.st_mtime, "size": st.st_size, "status": "ok"}

    calls = {"n": 0}

    def _fake_changed(self):
        calls["n"] += 1
        return {str(f.resolve())}

    monkeypatch.setattr(cd.GitCtx, "changed_abs_paths", _fake_changed)
    ctx = cd.GitCtx(str(tmp_path))
    fresh, stale, missing = cd.bulk_partition([str(f)], {str(f): row}, strategy="git", git_ctx=ctx)

    assert str(f.resolve()) in stale
    assert calls["n"] == 1
