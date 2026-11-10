from fastapi.testclient import TestClient


def test_jsui_keep_only_normalize_then_apply(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("print('a')\n", encoding="utf-8")
    (root / "b.py").write_text("print('b')\n", encoding="utf-8")
    (root / "c.txt").write_text("hello\n", encoding="utf-8")

    client = TestClient(jsserver.app)

    res = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert res.status_code == 200

    norm = client.post(
        "/api/path/normalize",
        json={
            "text": "a.py:10\nb.py\nmissing.py\n",
            "options": {"smart": True, "autocorrect": True, "use_llm": False, "dir_prefix": False},
        },
    )
    assert norm.status_code == 200
    payload = norm.json()
    assert "items" in payload
    assert "requested_rel" in payload
    assert sorted(payload["requested_rel"]) == ["a.py", "b.py"]

    apply_res = client.post("/api/keep_only/apply", json={"keep_rels": payload["requested_rel"]})
    assert apply_res.status_code == 200
    applied = apply_res.json()
    assert applied["ok"] is True
    assert applied["kept"] == 2

    res2 = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert res2.status_code == 200
    visible = {item["rel_path"] for item in res2.json().get("files", [])}
    assert visible == {"a.py", "b.py"}
