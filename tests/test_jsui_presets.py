
from fastapi.testclient import TestClient


def _reset_user_presets(jsserver) -> None:
    ctx = jsserver._init_global_ctx()
    with ctx.config_lock:
        cfg = ctx.config.get_config()
        cfg["user_presets"] = {}
        ctx.config.save_config()


def test_jsui_presets_crud_and_load(tmp_path):
    import codemerge.jsui.server as jsserver

    _reset_user_presets(jsserver)
    client = TestClient(jsserver.app)

    res = client.get("/api/presets")
    assert res.status_code == 200
    assert res.json()["names"] == []

    # Make the change observable via preset load.
    res = client.put("/api/config", json={"file_types": {"md": False}})
    assert res.status_code == 200

    res = client.post("/api/presets", json={"name": "Docs", "preset": {"file_types": {"md": True}}})
    assert res.status_code == 200
    assert "Docs" in res.json().get("names", [])

    res = client.get("/api/presets")
    assert res.status_code == 200
    assert "Docs" in res.json().get("names", [])

    res = client.post("/api/presets/load", json={"name": "Docs"})
    assert res.status_code == 200
    cfg = res.json()["config"]
    assert cfg["file_types"]["md"] is True

    res = client.delete("/api/presets/Docs")
    assert res.status_code == 200
    assert res.json()["deleted"] is True

    res = client.get("/api/presets")
    assert res.status_code == 200
    assert res.json()["names"] == []


def test_jsui_merge_prompt_template_flow(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("hello", encoding="utf-8")

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200
    output_file = root / "merged.txt"
    res = client.post(
        "/api/merge",
        json={
            "path": str(root),
            "selected_paths": [str(f)],
            "prompt_template": "CUSTOM_SYSTEM_PROMPT\n{merged_content}\nEND_PROMPT",
            "output_file": str(output_file),
        },
    )
    assert res.status_code == 200
    text = output_file.read_text(encoding="utf-8")
    assert text.startswith("CUSTOM_SYSTEM_PROMPT")
    assert text.rstrip().endswith("END_PROMPT")
