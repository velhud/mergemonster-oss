from __future__ import annotations

from pathlib import Path


def _scan_context(jsserver, root: Path, file_path: Path):
    meta = jsserver.FileMeta(
        abs_path=file_path.resolve(),
        rel_path=file_path.relative_to(root),
        size_bytes=file_path.stat().st_size,
        mtime=file_path.stat().st_mtime,
        token_count=1,
        ext=file_path.suffix,
        category=jsserver.Category.BACKEND,
    )
    return jsserver.ScanContext(
        root=root.resolve(),
        files=[meta],
        aggregate_tokens=1,
        aggregate_bytes=meta.size_bytes,
        by_ext={file_path.suffix: [meta.size_bytes]},
        by_category={jsserver.Category.BACKEND: [meta.size_bytes]},
    )


def test_merge_job_uses_input_snapshot_after_project_b_scan(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root_a = tmp_path / "repo-a"
    root_b = tmp_path / "repo-b"
    root_a.mkdir()
    root_b.mkdir()
    file_a = root_a / "a.py"
    file_b = root_b / "b.py"
    file_a.write_text("print('a')\n", encoding="utf-8")
    file_b.write_text("print('b')\n", encoding="utf-8")

    srv = jsserver._init_global_ctx()
    srv.config.update_config({"last_directory": str(root_a), "summary_default_mode": "CODE"})
    srv.core.configure(srv.config.get_config())
    srv.core.scan_ctx = _scan_context(jsserver, root_a, file_a)
    snapshot = jsserver._capture_job_input_snapshot(srv, root_a)

    srv.config.update_config({"last_directory": str(root_b), "summary_default_mode": "EXCLUDE"})
    srv.core.configure(srv.config.get_config())
    srv.core.scan_ctx = _scan_context(jsserver, root_b, file_b)
    srv.core.per_file_modes = {str(file_a.resolve()): jsserver.FileMode.EXCLUDE}

    captured: dict[str, object] = {}

    def fake_merge_files(_processor, *, directory, files_to_merge, output_file, **_kwargs):
        captured["directory"] = directory
        captured["files_to_merge"] = list(files_to_merge)
        Path(output_file).write_text("merged\n", encoding="utf-8")

    monkeypatch.setattr(jsserver.merge_basic_mod, "merge_files", fake_merge_files)
    with srv.jobs_lock:
        srv.jobs["job-a"] = {"id": "job-a", "status": "running", "type": "merge"}

    output_file = root_a / "merged.txt"
    jsserver._run_merge_single_job(
        srv,
        "job-a",
        root_a,
        {"selected_paths": [str(file_a)], "output_file": str(output_file)},
        snapshot,
    )

    assert captured["directory"] == str(root_a)
    assert captured["files_to_merge"] == [str(file_a.resolve())]
    with srv.jobs_lock:
        job = srv.jobs["job-a"]
    assert job["status"] == "done"
    assert job["result"]["output_path"] == str(output_file)


def test_repo_summary_job_uses_input_snapshot_after_project_b_scan(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root_a = tmp_path / "repo-a"
    root_b = tmp_path / "repo-b"
    root_a.mkdir()
    root_b.mkdir()
    file_a = root_a / "a.py"
    file_b = root_b / "b.py"
    file_a.write_text("print('a')\n", encoding="utf-8")
    file_b.write_text("print('b')\n", encoding="utf-8")

    srv = jsserver._init_global_ctx()
    srv.config.update_config({"last_directory": str(root_a)})
    srv.core.configure(srv.config.get_config())
    srv.core.scan_ctx = _scan_context(jsserver, root_a, file_a)
    snapshot = jsserver._capture_job_input_snapshot(srv, root_a)

    srv.config.update_config({"last_directory": str(root_b)})
    srv.core.configure(srv.config.get_config())
    srv.core.scan_ctx = _scan_context(jsserver, root_b, file_b)

    captured: dict[str, object] = {}

    def fake_ensure_repo_summary(*, root_abs, scan_files, **_kwargs):
        captured["root_abs"] = root_abs
        captured["scan_files"] = [str(meta.abs_path) for meta in scan_files]
        return {"root_abs": root_abs}

    monkeypatch.setattr(jsserver, "ensure_repo_summary", fake_ensure_repo_summary)
    monkeypatch.setattr(jsserver, "render_repo_summary", lambda obj, **_kwargs: f"repo {obj['root_abs']}")
    with srv.jobs_lock:
        srv.jobs["job-a"] = {"id": "job-a", "status": "running", "type": "summarize_repo"}

    jsserver._run_summarize_repo_job(srv, "job-a", "full", snapshot.config, snapshot)

    assert captured["root_abs"] == str(root_a.resolve())
    assert captured["scan_files"] == [str(file_a.resolve())]
    with srv.jobs_lock:
        job = srv.jobs["job-a"]
    assert job["status"] == "done"
    assert job["result"]["summary_text"] == f"repo {root_a.resolve()}"


def test_job_detail_exposes_input_snapshot_metadata(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver
    from fastapi.testclient import TestClient

    root = tmp_path / "repo"
    root.mkdir()
    file_path = root / "a.py"
    file_path.write_text("print('a')\n", encoding="utf-8")

    srv = jsserver._init_global_ctx()
    srv.config.update_config({"last_directory": str(root), "summary_default_mode": "CODE"})
    srv.core.configure(srv.config.get_config())
    srv.core.scan_ctx = _scan_context(jsserver, root, file_path)

    def fake_merge_files(_processor, *, output_file, **_kwargs):
        Path(output_file).write_text("merged\n", encoding="utf-8")

    monkeypatch.setattr(jsserver.merge_basic_mod, "merge_files", fake_merge_files)
    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge_job",
        json={"path": str(root), "selected_paths": [str(file_path)], "output_file": str(root / "merged.txt")},
    )

    assert res.status_code == 200
    job = client.get(f"/api/jobs/{res.json()['job_id']}").json()
    assert job["input_snapshot_id"]
    assert job["input_snapshot_root"] == str(root)
    assert job["input_snapshot_created_at"]
