from __future__ import annotations


def _schema_ref(operation: dict) -> str:
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    return schema.get("$ref", "")


def test_core_agent_routes_have_typed_openapi_responses() -> None:
    import codemerge.jsui.server as jsserver

    schema = jsserver.app.openapi()
    paths = schema["paths"]

    assert _schema_ref(paths["/api/jobs"]["get"]).endswith("/JobsListResponse")
    assert _schema_ref(paths["/api/jobs/{job_id}"]["get"]).endswith("/JobSnapshotResponse")
    assert _schema_ref(paths["/api/jobs/{job_id}/cancel"]["post"]).endswith("/JobCancelResponse")
    assert _schema_ref(paths["/api/session/current"]["get"]).endswith("/WorkspaceSessionEnvelope")
    assert _schema_ref(paths["/api/session/export"]["post"]).endswith("/WorkspaceSessionEnvelope")
    assert _schema_ref(paths["/api/session/import"]["post"]).endswith("/WorkspaceSessionMutationResponse")
    assert _schema_ref(paths["/api/session/select"]["post"]).endswith("/WorkspaceSessionMutationResponse")
    assert _schema_ref(paths["/api/provider/preflight"]["post"]).endswith("/ProviderPreflightResponse")
    assert _schema_ref(paths["/api/config"]["get"]).endswith("/ConfigEnvelope")
    assert _schema_ref(paths["/api/config"]["put"]).endswith("/ConfigEnvelope")
    assert _schema_ref(paths["/api/path/exists"]["post"]).endswith("/PathExistsResponse")
    assert _schema_ref(paths["/api/presets"]["get"]).endswith("/PresetNamesResponse")
    assert _schema_ref(paths["/api/scan"]["post"]).endswith("/ScanResponse")
    assert _schema_ref(paths["/api/filters/apply"]["post"]).endswith("/ScanResponse")
    assert _schema_ref(paths["/api/output/concat"]["post"]).endswith("/OutputConcatResponse")
    assert _schema_ref(paths["/api/output/zip"]["post"]).endswith("/OutputZipResponse")
    assert _schema_ref(paths["/api/file/preview"]["post"]).endswith("/FilePreviewResponse")
    assert _schema_ref(paths["/api/pair_codebases"]["post"]).endswith("/PairResponse")
    assert _schema_ref(paths["/api/pair_ai"]["post"]).endswith("/PairAiResponse")
    assert _schema_ref(paths["/api/restore"]["post"]).endswith("/RestoreResponse")

    components = schema["components"]["schemas"]
    job = components["JobSnapshotResponse"]["properties"]
    for field in (
        "id",
        "type",
        "status",
        "progress",
        "progress_pct",
        "result",
        "error",
        "output_paths",
        "cancel_supported",
        "input_snapshot_id",
        "provider_preflight",
        "provider_accounting",
    ):
        assert field in job

    provider = components["ProviderPreflightResponse"]["properties"]
    for field in (
        "action",
        "requires_confirmation",
        "confirmation_token",
        "scope",
        "budget",
        "provider_policy",
        "risks",
    ):
        assert field in provider

    cancel = components["JobCancelResponse"]["properties"]
    for field in ("ok", "job_id", "status", "cancel_supported", "message"):
        assert field in cancel

    session = components["WorkspaceSessionModel"]["properties"]
    for field in (
        "version",
        "id",
        "root",
        "config",
        "scan",
        "selection",
        "modes",
        "outputs",
        "provider_policy",
        "diagnostics",
    ):
        assert field in session

    scan = components["ScanResponse"]["properties"]
    for field in ("root", "files", "aggregate_tokens", "aggregate_bytes", "count"):
        assert field in scan

    preview = components["FilePreviewResponse"]["properties"]
    for field in ("text", "file_path", "size_bytes", "max_kb", "shown_kb", "truncated"):
        assert field in preview

    pair_ai = components["PairAiResponse"]["properties"]
    for field in ("report", "output_path", "provider_accounting"):
        assert field in pair_ai


def test_typed_routes_still_return_legacy_json_shapes(tmp_path) -> None:
    import codemerge.jsui.server as jsserver
    from fastapi.testclient import TestClient

    root = tmp_path / "repo"
    root.mkdir()
    srv = jsserver._init_global_ctx()
    srv.config.update_config({"last_directory": str(root)})
    with srv.jobs_lock:
        srv.jobs["job-a"] = {
            "id": "job-a",
            "status": "done",
            "type": "scan",
            "progress": "done",
            "progress_pct": 100,
            "result": {"root": str(root)},
        }

    client = TestClient(jsserver.app)
    job = client.get("/api/jobs/job-a").json()
    assert job["id"] == "job-a"
    assert job["result"]["root"] == str(root)
    assert job["output_paths"] == []

    jobs = client.get("/api/jobs").json()
    assert jobs["count"] == 1
    assert jobs["jobs"][0]["id"] == "job-a"

    session = client.get("/api/session/current").json()["session"]
    assert session["root"] == str(root.resolve())
    assert "config" in session
    assert "selection" in session


def test_broader_typed_routes_still_return_legacy_json_shapes(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver
    from fastapi.testclient import TestClient

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    out_folder = root / "out"
    out_folder.mkdir()
    (out_folder / jsserver.OUTPUT_MARKER).touch()
    (out_folder / "part.txt").write_text("part", encoding="utf-8")

    client = TestClient(jsserver.app)
    assert client.post("/api/scan", json={"path": str(root), "include_all": True}).status_code == 200

    config = client.get("/api/config").json()
    assert "config" in config

    exists = client.post("/api/path/exists", json={"path": str(source)}).json()
    assert exists == {"exists": True, "is_file": True, "is_dir": False}

    concat = client.post("/api/output/concat", json={"folder": str(out_folder)}).json()
    assert concat["text"] == "part"
    assert concat["files"] == ["part.txt"]
    assert concat["count"] == 1

    zip_res = client.post("/api/output/zip", json={"folder": str(out_folder)}).json()
    assert zip_res["zip_path"].endswith("out.zip")
    assert "download_token=" in zip_res["download_url"]

    preview = client.post("/api/file/preview", json={"file_path": str(source), "max_kb": 1}).json()
    assert preview["file_path"] == str(source.resolve())
    assert preview["truncated"] is False

    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "pair_codebases", lambda *args, **kwargs: None)
    pair = client.post(
        "/api/pair_codebases",
        json={"src_a": str(root), "src_b": str(root), "dest_dir": str(root / "paired")},
    ).json()
    assert pair == {"ok": True, "dest": str((root / "paired").resolve())}
