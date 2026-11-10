from fastapi.testclient import TestClient


def test_payload_to_ui_state_coerces_merge_numbers_and_content_mode():
    import codemerge.jsui.server as jsserver

    ctx = jsserver._init_global_ctx()
    cfg = ctx.config.get_config().copy()
    warnings: list[str] = []

    ui = jsserver._payload_to_ui_state(
        {
            "split_into_n": "10",
            "token_limit": None,
            "ignore_binary": "false",
            "content_mode": "NOT_A_MODE",
            "filters": {"categories": ["BACKEND", "NOT_A_CATEGORY"]},
        },
        cfg,
        warnings=warnings,
    )

    assert ui.merge.split_into_n == 10
    assert ui.merge.token_limit == int(cfg.get("token_limit", 1_048_576))
    assert ui.scan.ignore_binary is False
    assert ui.summary.content_mode == "code_only"
    assert ui.filters.category_mask[jsserver.Category.BACKEND] is True
    assert ui.filters.category_mask[jsserver.Category.FRONTEND] is False
    assert any("Invalid content_mode" in w for w in warnings)


def test_payload_to_ui_state_accepts_skeleton_content_modes():
    import codemerge.jsui.server as jsserver

    cfg = jsserver._init_global_ctx().config.get_config().copy()
    warnings: list[str] = []

    ui = jsserver._payload_to_ui_state(
        {"content_mode": "short_plus_skeleton"},
        cfg,
        warnings=warnings,
    )

    assert ui.summary.content_mode == "short_plus_skeleton"
    assert warnings == []


def test_content_mode_map_from_plan_round_trips_skeleton_modes():
    import codemerge.jsui.server as jsserver

    mapping = jsserver._content_mode_map_from_plan(
        {
            "/repo/a.py": jsserver.FileMode.SKELETON,
            "/repo/b.py": jsserver.FileMode.CODE_PLUS_SKELETON,
            "/repo/c.py": jsserver.FileMode.SUMMARY_SHORT_PLUS_SKELETON,
            "/repo/d.py": jsserver.FileMode.SUMMARY_LONG_PLUS_SKELETON,
            "/repo/e.py": jsserver.FileMode.CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON,
            "/repo/f.py": jsserver.FileMode.API_SURFACE,
        }
    )

    assert mapping["/repo/a.py"] == "skeleton"
    assert mapping["/repo/b.py"] == "code_plus_skeleton"
    assert mapping["/repo/c.py"] == "summary_short_plus_skeleton"
    assert mapping["/repo/d.py"] == "summary_long_plus_skeleton"
    assert mapping["/repo/e.py"] == "code_plus_summary_short_plus_skeleton"
    assert mapping["/repo/f.py"] == "skeleton"


def test_settings_accepts_skeleton_default_mode(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    srv = jsserver._init_global_ctx()
    srv.config.update_config({"last_directory": str(root)})

    client = TestClient(jsserver.app)
    response = client.put(
        "/api/settings",
        json={"path": str(root), "summary_default_mode": "SUMMARY_SHORT_PLUS_SKELETON"},
    )

    assert response.status_code == 200
    assert srv.config.get_config()["summary_default_mode"] == "SUMMARY_SHORT_PLUS_SKELETON"


def test_payload_to_ui_state_uses_model_constants_when_cfg_missing():
    import codemerge.jsui.server as jsserver

    ui = jsserver._payload_to_ui_state({"filters": {}}, {}, warnings=[])
    assert ui.filters.token_max == jsserver.DEFAULT_TOKEN_MAX
    assert ui.filters.size_max_kb == jsserver.DEFAULT_SIZE_MAX


def test_endpoints_accept_string_numbers_for_split_into_n(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("hello", encoding="utf-8")

    captured = {}
    srv = jsserver._init_global_ctx()

    def fake_merge_files_into_multiple(*_args, **kwargs):
        captured["split_into_n"] = kwargs.get("num_text_files")
        return None

    client = TestClient(jsserver.app)
    monkeypatch.setattr(srv.processor, "merge_files_into_multiple", fake_merge_files_into_multiple)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200
    res = client.post(
        "/api/merge/multiple",
        json={"path": str(root), "selected_paths": [str(f)], "split_into_n": "3"},
    )
    assert res.status_code == 200
    assert captured.get("split_into_n") == 3
