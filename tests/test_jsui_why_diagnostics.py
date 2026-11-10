from fastapi.testclient import TestClient


def test_jsui_why_returns_meta(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.py"
    f.write_text("print('a')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert res.status_code == 200

    why = client.post("/api/why", json={"path": str(root), "file_path": str(f), "filters": {}})
    assert why.status_code == 200
    payload = why.json()
    assert "kept" in payload
    assert "reasons" in payload
    assert "meta" in payload
    meta = payload["meta"]
    assert meta["abs_path"].endswith("a.py")
    assert meta["rel_path"] == "a.py"
    assert isinstance(meta["size_bytes"], int)
    assert "token_count" in meta
    assert isinstance(meta["mtime"], float)
    assert meta["category"]


def test_jsui_why_reports_custom_excluded_and_ignore(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "proj_why"
    root.mkdir()
    f = root / "a.py"
    f.write_text("print('a')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    # Ensure config doesn't globally exclude our temp folder/file names.
    client.put("/api/settings", json={"excluded_dirs": [], "excluded_files": [], "custom_excluded": []})
    all_cats = ["BACKEND", "FRONTEND", "DOCS", "CONFIG", "ASSETS", "BINARY"]
    baseline_filters = {
        "name_pattern": "",
        "name_filter_glob": False,
        "token_min": 0,
        "token_max": 10_000_000,
        "size_min": 0.0,
        "size_max": 100_000.0,
        "date_after": "",
        "date_before": "",
        "categories": all_cats,
        "content_search": "",
        "content_regex": False,
        "content_case_sensitive": False,
        "content_max_kb": 256,
        "content_hide_duplicates": False,
        "content_mark_duplicates": True,
        "always_include_docs": True,
    }
    # Persist an exclusion by relative path.
    put = client.put("/api/settings", json={"custom_excluded": ["a.py"]})
    assert put.status_code == 200

    scan_toggles = {
        "use_git_files": False,
        "git_include_untracked": False,
        "git_only_changed": False,
        "git_since_ref": "",
        "git_until_ref": "HEAD",
        "exclude_vendored": False,
    }

    why = client.post(
        "/api/why",
        json={"path": str(root), "file_path": str(f), "filters": baseline_filters, **scan_toggles},
    )
    assert why.status_code == 200
    payload = why.json()
    assert payload["kept"] is False
    assert any("Custom excluded" == r for r in payload.get("reasons", []))

    # When the UI ignores persisted excludes, /api/why should report the ignored state.
    why2 = client.post(
        "/api/scan",
        json={"path": str(root), "include_all": True, "ignore_custom_excluded": True, "filters": baseline_filters, **scan_toggles},
    )
    assert why2.status_code == 200

    why3 = client.post(
        "/api/why",
        json={"path": str(root), "file_path": str(f), "filters": baseline_filters, "ignore_custom_excluded": True, **scan_toggles},
    )
    assert why3.status_code == 200
    payload2 = why3.json()
    assert payload2["kept"] is True
    assert any("Custom excluded (ignored)" == r for r in payload2.get("reasons", []))


def test_jsui_scan_warns_on_read_error(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo_read"
    root.mkdir()
    f = root / "bin.dat"
    f.write_bytes(b"\x00\xff\x00")

    client = TestClient(jsserver.app)
    all_cats = ["BACKEND", "FRONTEND", "DOCS", "CONFIG", "ASSETS", "BINARY"]
    res = client.post(
        "/api/scan",
        json={
            "path": str(root),
            "include_all": True,
            "ignore_binary": False,
            "filters": {"categories": all_cats},
        },
    )
    assert res.status_code == 200
    data = res.json()
    warnings = data.get("warnings") or []
    assert any("unreadable" in str(w).lower() for w in warnings)


def test_jsui_why_reports_read_error(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo_why_read"
    root.mkdir()
    f = root / "bin.dat"
    f.write_bytes(b"\x00\xff\x00")

    client = TestClient(jsserver.app)
    all_cats = ["BACKEND", "FRONTEND", "DOCS", "CONFIG", "ASSETS", "BINARY"]
    baseline_filters = {
        "name_pattern": "",
        "name_filter_glob": False,
        "token_min": 0,
        "token_max": 10_000_000,
        "size_min": 0.0,
        "size_max": 100_000.0,
        "date_after": "",
        "date_before": "",
        "categories": all_cats,
        "content_search": "",
        "content_regex": False,
        "content_case_sensitive": False,
        "content_max_kb": 256,
        "content_hide_duplicates": False,
        "content_mark_duplicates": True,
        "always_include_docs": True,
    }
    scan_toggles = {
        "use_git_files": False,
        "git_include_untracked": False,
        "git_only_changed": False,
        "git_since_ref": "",
        "git_until_ref": "HEAD",
        "exclude_vendored": False,
    }
    res = client.post(
        "/api/why",
        json={"path": str(root), "file_path": str(f), "filters": baseline_filters, **scan_toggles},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["meta"]["read_error"] is True
    assert any("read error" in r.lower() for r in payload.get("reasons", []))


def test_jsui_diagnostics_reports_read_errors(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo_diag_read"
    root.mkdir()
    f = root / "bin.dat"
    f.write_bytes(b"\x00\xff\x00")

    client = TestClient(jsserver.app)
    res = client.post("/api/diagnostics", json={"path": str(root), "include_all": True})
    assert res.status_code == 200
    report = res.json().get("report", "")
    assert "Read errors:" in report


def test_jsui_diagnostics_respects_max_depth(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo_depth"
    deep = root / "level1" / "level2"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("deep", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/diagnostics",
        json={"path": str(root), "include_all": True, "max_walk_depth": 1},
    )
    assert res.status_code == 200
    report = res.json().get("report", "")
    assert "depth_limit" in report


def test_jsui_diagnostics_reports_size_limit_reason(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo_size"
    root.mkdir()
    (root / "big.txt").write_text("x" * 2000, encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/diagnostics",
        json={"path": str(root), "include_all": True, "max_file_size_mb": 0.000001},
    )
    assert res.status_code == 200
    report = res.json().get("report", "")
    assert "size_limit" in report
