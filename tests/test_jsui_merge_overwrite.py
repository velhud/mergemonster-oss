from fastapi.testclient import TestClient

from codemerge.jsui.path_policy import OUTPUT_MARKER


def test_merge_multiple_overwrite_flag(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("hello", encoding="utf-8")

    srv = jsserver._init_global_ctx()

    def fake_merge_files_into_multiple(*_args, **_kwargs):
        return None

    monkeypatch.setattr(srv.processor, "merge_files_into_multiple", fake_merge_files_into_multiple)

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200

    output_folder = root / "out_split"
    output_folder.mkdir()
    (output_folder / OUTPUT_MARKER).touch()
    sentinel = output_folder / "keep.txt"
    sentinel.write_text("old", encoding="utf-8")
    res = client.post(
        "/api/merge/multiple",
        json={
            "path": str(root),
            "selected_paths": [str(f)],
            "split_into_n": 2,
            "overwrite": True,
            "output_folder": str(output_folder),
        },
    )
    assert res.status_code == 200
    assert not sentinel.exists()
    assert (output_folder / OUTPUT_MARKER).exists()


def test_merge_token_split_overwrite_flag(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "b.txt"
    f.write_text("hello", encoding="utf-8")

    srv = jsserver._init_global_ctx()

    def fake_merge_files_token_split(*_args, **_kwargs):
        return None

    monkeypatch.setattr(srv.processor, "merge_files_token_split", fake_merge_files_token_split)

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200

    output_folder = root / "out_tok"
    output_folder.mkdir()
    (output_folder / OUTPUT_MARKER).touch()
    sentinel = output_folder / "keep.txt"
    sentinel.write_text("old", encoding="utf-8")
    res = client.post(
        "/api/merge/token_split",
        json={
            "path": str(root),
            "selected_paths": [str(f)],
            "token_limit": 1000,
            "overwrite": True,
            "output_folder": str(output_folder),
        },
    )
    assert res.status_code == 200
    assert not sentinel.exists()
    assert (output_folder / OUTPUT_MARKER).exists()


def test_merge_multiple_overwrite_rejects_unmarked_folder(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("hello", encoding="utf-8")

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200

    output_folder = root / "out_split"
    output_folder.mkdir()
    sentinel = output_folder / "keep.txt"
    sentinel.write_text("old", encoding="utf-8")
    res = client.post(
        "/api/merge/multiple",
        json={
            "path": str(root),
            "selected_paths": [str(f)],
            "split_into_n": 2,
            "overwrite": True,
            "output_folder": str(output_folder),
        },
    )

    assert res.status_code == 409
    assert "non-generated folder" in res.json()["error"]
    assert sentinel.exists()


def test_merge_multiple_string_false_overwrite_does_not_delete_marked_folder(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("hello", encoding="utf-8")

    srv = jsserver._init_global_ctx()

    def fake_merge_files_into_multiple(*_args, **_kwargs):
        return None

    monkeypatch.setattr(srv.processor, "merge_files_into_multiple", fake_merge_files_into_multiple)

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200

    output_folder = root / "out_split"
    output_folder.mkdir()
    (output_folder / OUTPUT_MARKER).touch()
    sentinel = output_folder / "keep.txt"
    sentinel.write_text("old", encoding="utf-8")
    res = client.post(
        "/api/merge/multiple",
        json={
            "path": str(root),
            "selected_paths": [str(f)],
            "split_into_n": 2,
            "overwrite": "false",
            "output_folder": str(output_folder),
        },
    )

    assert res.status_code == 409
    assert sentinel.exists()


def test_merge_token_split_overwrite_rejects_unmarked_folder(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "b.txt"
    f.write_text("hello", encoding="utf-8")

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200

    output_folder = root / "out_tok"
    output_folder.mkdir()
    sentinel = output_folder / "keep.txt"
    sentinel.write_text("old", encoding="utf-8")
    res = client.post(
        "/api/merge/token_split",
        json={
            "path": str(root),
            "selected_paths": [str(f)],
            "token_limit": 1000,
            "overwrite": True,
            "output_folder": str(output_folder),
        },
    )

    assert res.status_code == 409
    assert "non-generated folder" in res.json()["error"]
    assert sentinel.exists()
