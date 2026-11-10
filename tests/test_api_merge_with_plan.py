import os

from codemerge.models import FileMode


def test_merge_with_plan_integration(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.filemerger.api as fapi

    root = tmp_path / "repo"
    root.mkdir()
    a = root / "a.py"
    a.write_text("print('A')\n", encoding="utf-8")
    b = root / "b.py"
    b.write_text("print('B')\n", encoding="utf-8")
    c = root / "c.py"
    c.write_text("print('C')\n", encoding="utf-8")
    out = root / "merged.md"

    # Stub orchestrator.ensure_summaries used by merge_with_plan
    def fake_ensure(paths, kind, file_processor, app_config, progress_cb=None):
        mp = {}
        for p in paths:
            base = os.path.basename(p)
            if base == "a.py":
                mp[p] = "A (func): do a"
            elif base == "b.py":
                mp[p] = "B (func): do b"
            elif base == "c.py":
                mp[p] = "C (func): do c"
            else:
                mp[p] = None
        return mp, []

    monkeypatch.setattr(fapi, "_require_ensure_summaries", lambda: fake_ensure)

    fp = FileProcessor()
    cfg = AppConfig().get_config()
    plan = {
        str(a): FileMode.CODE_PLUS_SHORT,
        str(b): FileMode.SUMMARY_SHORT,
        str(c): FileMode.CODE_PLUS_LONG,
    }
    result = fp.merge_with_plan(
        directory=str(root),
        plan=plan,
        output_file=str(out),
        markdown_fences=False,
        markdown_toc=False,
        app_config=cfg,
    )
    assert result.ok and out.exists()
    text = out.read_text(encoding="utf-8")
    assert "# A (func): do a" in text
    assert "print('A')" in text
    assert "B (func): do b" in text
    assert "print('B')" not in text
    assert "# C (func): do c" in text
    assert "print('C')" in text


def test_merge_with_plan_skips_vendored_summary_only(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.filemerger.api as fapi
    from codemerge.models import FileMode

    root = tmp_path / "repo"
    root.mkdir()
    vendor_short = root / "vendor_short.py"
    vendor_short.write_text("print('vendored short')\n", encoding="utf-8")
    vendor_long = root / "vendor_long.py"
    vendor_long.write_text("print('vendored long')\n", encoding="utf-8")
    out = root / "merged.md"

    calls = {"n": 0}

    def fake_ensure(*_args, **_kwargs):
        calls["n"] += 1
        return ({}, [])

    monkeypatch.setattr(fapi, "_require_ensure_summaries", lambda: fake_ensure)

    fp = FileProcessor()
    monkeypatch.setattr(fp, "git_vendored_set", lambda _root, _rels: set(_rels))

    cfg = AppConfig().get_config()
    cfg["summarize_vendored"] = False

    result = fp.merge_with_plan(
        directory=str(root),
        plan={
            str(vendor_short): FileMode.SUMMARY_SHORT,
            str(vendor_long): FileMode.SUMMARY_LONG,
        },
        output_file=str(out),
        markdown_fences=False,
        markdown_toc=False,
        app_config=cfg,
    )

    assert result.ok and out.exists()
    assert calls["n"] == 0
    merged = out.read_text(encoding="utf-8")
    assert "Summary was unavailable" in merged
    assert "print('vendored short')" not in merged
    assert "print('vendored long')" not in merged


def test_merge_with_plan_skips_vendored_code_plus_as_code(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.filemerger.api as fapi
    from codemerge.models import FileMode

    root = tmp_path / "repo"
    root.mkdir()
    vendor = root / "vendor.py"
    vendor.write_text("print('vendored')\n", encoding="utf-8")
    out = root / "merged.md"

    calls = {"n": 0}

    def fake_ensure(*_args, **_kwargs):
        calls["n"] += 1
        return ({}, [])

    monkeypatch.setattr(fapi, "_require_ensure_summaries", lambda: fake_ensure)

    fp = FileProcessor()
    monkeypatch.setattr(fp, "git_vendored_set", lambda _root, _rels: set(_rels))

    cfg = AppConfig().get_config()
    cfg["summarize_vendored"] = False

    result = fp.merge_with_plan(
        directory=str(root),
        plan={str(vendor): FileMode.CODE_PLUS_SHORT},
        output_file=str(out),
        markdown_fences=False,
        markdown_toc=False,
        app_config=cfg,
    )

    assert result.ok and out.exists()
    assert calls["n"] == 0
    merged = out.read_text(encoding="utf-8")
    assert "Summary was unavailable" not in merged
    assert "print('vendored')" in merged


def test_merge_with_plan_skeleton_modes(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.filemerger.api as fapi
    from codemerge.models import FileMode

    root = tmp_path / "repo"
    root.mkdir()
    skel = root / "skel.py"
    skel.write_text("def only_signature():\n    return 1\n", encoding="utf-8")
    combo = root / "combo.py"
    combo.write_text("def with_code():\n    return 2\n", encoding="utf-8")
    summarized = root / "summarized.py"
    summarized.write_text("def hidden_body():\n    return 3\n", encoding="utf-8")
    out = root / "merged.md"

    def fake_ensure(paths, kind, *_args, **_kwargs):
        assert kind == "short"
        return ({p: f"{os.path.basename(p)} summary" for p in paths}, [])

    monkeypatch.setattr(fapi, "_require_ensure_summaries", lambda: fake_ensure)

    fp = FileProcessor()
    cfg = AppConfig().get_config()

    result = fp.merge_with_plan(
        directory=str(root),
        plan={
            str(skel): FileMode.SKELETON,
            str(combo): FileMode.CODE_PLUS_SKELETON,
            str(summarized): FileMode.SUMMARY_SHORT_PLUS_SKELETON,
        },
        output_file=str(out),
        markdown_fences=False,
        markdown_toc=False,
        app_config=cfg,
    )

    assert result.ok and out.exists()
    merged = out.read_text(encoding="utf-8")
    assert "# SKELETON" in merged
    assert "def only_signature():" in merged
    assert "return 1" not in merged
    assert "def with_code():" in merged
    assert "return 2" in merged
    assert "# summarized.py summary" in merged
    assert "def hidden_body():" in merged
    assert "return 3" not in merged


def test_merge_with_plan_code_plus_summary_plus_skeleton_mode(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.filemerger.api as fapi
    from codemerge.models import FileMode

    root = tmp_path / "repo"
    root.mkdir()
    path = root / "all.py"
    path.write_text("def all_parts():\n    return 'body'\n", encoding="utf-8")
    out = root / "merged.md"

    def fake_ensure(paths, kind, *_args, **_kwargs):
        assert kind == "short"
        return ({p: "All parts summary" for p in paths}, [])

    monkeypatch.setattr(fapi, "_require_ensure_summaries", lambda: fake_ensure)

    fp = FileProcessor()
    cfg = AppConfig().get_config()
    result = fp.merge_with_plan(
        directory=str(root),
        plan={str(path): FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON},
        output_file=str(out),
        markdown_fences=False,
        markdown_toc=False,
        app_config=cfg,
    )

    assert result.ok and out.exists()
    merged = out.read_text(encoding="utf-8")
    assert "# All parts summary" in merged
    assert "# SKELETON" in merged
    assert "def all_parts():" in merged
    assert "return 'body'" in merged


def test_merge_with_plan_vendored_summary_plus_skeleton_downgrades_to_skeleton(monkeypatch, tmp_path):
    from codemerge.filemerger import AppConfig, FileProcessor
    import codemerge.filemerger.api as fapi
    from codemerge.models import FileMode

    root = tmp_path / "repo"
    root.mkdir()
    vendor = root / "vendor.py"
    vendor.write_text("def vendored():\n    return 'body'\n", encoding="utf-8")
    out = root / "merged.md"
    calls = {"n": 0}

    def fake_ensure(*_args, **_kwargs):
        calls["n"] += 1
        return ({}, [])

    monkeypatch.setattr(fapi, "_require_ensure_summaries", lambda: fake_ensure)

    fp = FileProcessor()
    monkeypatch.setattr(fp, "git_vendored_set", lambda _root, _rels: set(_rels))

    cfg = AppConfig().get_config()
    cfg["summarize_vendored"] = False

    result = fp.merge_with_plan(
        directory=str(root),
        plan={str(vendor): FileMode.SUMMARY_SHORT_PLUS_SKELETON},
        output_file=str(out),
        markdown_fences=False,
        markdown_toc=False,
        app_config=cfg,
    )

    assert result.ok and out.exists()
    assert calls["n"] == 0
    merged = out.read_text(encoding="utf-8")
    assert "# SKELETON" in merged
    assert "def vendored():" in merged
    assert "return 'body'" not in merged
    assert "Summary was unavailable" not in merged


def test_modes_config_helpers(tmp_path):
    from codemerge.filemerger import FileProcessor
    from codemerge.models import FileMode

    fp = FileProcessor()
    cfg: dict[str, object] = {}

    abs_a = os.path.abspath(tmp_path / "a.py")
    abs_b = os.path.abspath(tmp_path / "b.py")
    abs_c = os.path.abspath(tmp_path / "c.py")

    saved = fp.save_modes_to_config(
        cfg,
        {
            str(abs_a): FileMode.SUMMARY_LONG,
            str(abs_b): FileMode.SKELETON,
            str(abs_c): FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
        },
    )
    assert cfg["per_file_modes"][abs_a] == FileMode.SUMMARY_LONG.name
    assert cfg["per_file_modes"][abs_b] == FileMode.SKELETON.name
    assert cfg["per_file_modes"][abs_c] == FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON.name
    assert saved[abs_b] == FileMode.SKELETON.name
    assert saved[abs_c] == FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON.name

    loaded = fp.get_modes_from_config(cfg)
    assert loaded[abs_a] is FileMode.SUMMARY_LONG
    assert loaded[abs_b] is FileMode.SKELETON
    assert loaded[abs_c] is FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON
