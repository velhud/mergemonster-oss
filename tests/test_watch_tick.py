import os

from codemerge.models import FileMode


def test_watch_tick_metrics(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.summarizer.shared.watch as watch_mod

    root = tmp_path / "repo"
    root.mkdir()
    a = root / "a.py"
    a.write_text("print('A')\n", encoding="utf-8")
    b = root / "b.py"
    b.write_text("print('B')\n", encoding="utf-8")

    def fake_ensure(paths, kind, file_processor, app_config, progress_cb=None):
        mp = {}
        for p in paths:
            mp[p] = f"{os.path.basename(p)} ok"
        return mp, []

    monkeypatch.setattr(watch_mod, "ensure_summaries", fake_ensure)

    plan = {
        str(a): FileMode.CODE_PLUS_SHORT,
        str(b): FileMode.SUMMARY_LONG,
    }
    fp = FileProcessor()
    cfg = AppConfig().get_config()
    res = watch_mod.tick(str(root), plan, fp, cfg)
    metrics = res["metrics"]
    assert metrics["requested_short"] == 1
    assert metrics["requested_long"] == 1
    assert metrics["requested_code_plus_short"] == 1
    assert metrics["requested_code_plus_long"] == 0
    assert metrics["ensured"] == 2
    assert metrics["errors"] == 0


def test_watch_tick_does_not_force_refresh(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.summarizer.shared.watch as watch_mod

    root = tmp_path / "repo"
    root.mkdir()
    path = root / "a.py"
    path.write_text("print('A')\n", encoding="utf-8")

    forces: list[bool] = []

    def fake_ensure(paths, kind, file_processor, app_config, progress_cb=None, *, force=False):
        forces.append(force)
        return (
            {p: f"{os.path.basename(p)} ok" for p in paths},
            [],
        )

    monkeypatch.setattr(watch_mod, "ensure_summaries", fake_ensure)

    plan = {str(path): FileMode.SUMMARY_SHORT}
    fp = FileProcessor()
    cfg = AppConfig().get_config()
    cfg["auto_refresh_summaries"] = False
    cfg["auto_summarize_missing"] = False

    res = watch_mod.tick(str(root), plan, fp, cfg)

    assert forces == [False]
    assert res["metrics"]["ensured"] == 1


def test_watch_tick_reports_errors(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.summarizer.shared.watch as watch_mod

    root = tmp_path / "repo"
    root.mkdir()
    path = root / "err.py"
    path.write_text("print('err')\n", encoding="utf-8")

    def fake_ensure(paths, kind, file_processor, app_config, progress_cb=None):
        return ({paths[0]: None}, list(paths))

    monkeypatch.setattr(watch_mod, "ensure_summaries", fake_ensure)

    plan = {str(path): FileMode.SUMMARY_SHORT}
    fp = FileProcessor()
    cfg = AppConfig().get_config()

    result = watch_mod.tick(str(root), plan, fp, cfg)
    assert result["metrics"]["errors"] == 1
    assert any(str(path) in p for p in result["error_paths"])


def test_watch_tick_skips_vendored_when_disabled(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.summarizer.shared.watch as watch_mod

    root = tmp_path / "repo"
    root.mkdir()
    path = root / "vendor.py"
    path.write_text("print('vendored')\n", encoding="utf-8")

    calls = {"n": 0}

    def fake_ensure(*_args, **_kwargs):  # pragma: no cover - should be skipped
        calls["n"] += 1
        return ({}, [])

    monkeypatch.setattr(watch_mod, "ensure_summaries", fake_ensure)
    monkeypatch.setattr(
        watch_mod,
        "git_vendored_set",
        lambda directory, rels: set(rels),
    )

    plan = {str(path): FileMode.SUMMARY_SHORT}
    fp = FileProcessor()
    cfg = AppConfig().get_config()
    cfg["summarize_vendored"] = False

    result = watch_mod.tick(str(root), plan, fp, cfg)
    assert result["metrics"]["requested_short"] == 1
    assert calls["n"] == 0
