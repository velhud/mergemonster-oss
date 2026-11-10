from fastapi.testclient import TestClient


def test_jsui_files_from_parent_returns_children(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    (root / "src" / "components" / "sub").mkdir(parents=True)
    f1 = root / "src" / "components" / "a.js"
    f2 = root / "src" / "components" / "b.js"
    f3 = root / "src" / "components" / "sub" / "c.js"
    f4 = root / "src" / "other" / "d.js"
    f4.parent.mkdir(parents=True, exist_ok=True)
    for f in (f1, f2, f3, f4):
        f.write_text("console.log('x')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert res.status_code == 200

    by_file = client.post("/api/files/from-parent", json={"path": str(f1)})
    assert by_file.status_code == 200
    data = by_file.json()
    paths = set(data.get("paths") or [])
    assert str(f1) in paths
    assert str(f2) in paths
    assert str(f3) in paths
    assert str(f4) not in paths

    by_dir = client.post("/api/files/from-parent", json={"path": str(f1.parent)})
    assert by_dir.status_code == 200
    data2 = by_dir.json()
    paths2 = set(data2.get("paths") or [])
    assert paths2 == paths


def test_jsui_files_from_parent_denies_outside_root(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("print('a')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert res.status_code == 200

    outside = tmp_path / "outside.py"
    outside.write_text("print('x')\n", encoding="utf-8")
    denied = client.post("/api/files/from-parent", json={"path": str(outside)})
    assert denied.status_code == 403
