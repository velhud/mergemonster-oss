import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from codemerge.models import Category, FileMeta


def test_repo_summary_job_uses_full_scan_scope(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    visible = root / "visible.py"
    hidden = root / "hidden.py"
    visible.write_text("print('visible')\n", encoding="utf-8")
    hidden.write_text("print('hidden')\n", encoding="utf-8")

    meta_visible = FileMeta(
        abs_path=visible,
        rel_path=Path("visible.py"),
        size_bytes=visible.stat().st_size,
        mtime=visible.stat().st_mtime,
        token_count=1,
        ext=".py",
        category=Category.BACKEND,
    )
    meta_hidden = FileMeta(
        abs_path=hidden,
        rel_path=Path("hidden.py"),
        size_bytes=hidden.stat().st_size,
        mtime=hidden.stat().st_mtime,
        token_count=1,
        ext=".py",
        category=Category.BACKEND,
    )

    seen: dict[str, list[str]] = {}

    def fake_ensure_repo_summary(
        *,
        root_abs,
        scan_files,
        file_processor,
        app_config,
        mode=None,
        progress_cb=None,
        scope_source=None,
    ):
        _ = root_abs
        _ = file_processor
        _ = app_config
        _ = mode
        _ = progress_cb
        seen["scope_source"] = scope_source
        seen["scan_files"] = [str(meta.abs_path) for meta in scan_files]
        return "repo summary"

    class FakeSummaryManager:
        def run(self, key, func, *args, **kwargs):
            _ = key
            return func(*args, **kwargs)

    ctx = SimpleNamespace(
        core=SimpleNamespace(
            scan_ctx=SimpleNamespace(root=root, files=[meta_visible, meta_hidden]),
            visible_files=[meta_visible],
            configure=lambda _cfg: None,
        ),
        summarization_manager=FakeSummaryManager(),
        processor=SimpleNamespace(count_tokens=lambda text: max(len(str(text)), 1)),
        jobs={},
        jobs_lock=threading.Lock(),
    )

    monkeypatch.setattr(jsserver, "ensure_repo_summary", fake_ensure_repo_summary)

    job_id = "job-1"
    ctx.jobs[job_id] = {"status": "running"}

    jsserver._run_summarize_repo_job(
        ctx,
        job_id,
        None,
        {"last_directory": str(root), "summary_prompt_version": 1, "full_summary_model": "test-model"},
    )

    assert seen["scan_files"] == [str(visible), str(hidden)]
    assert seen["scope_source"] == "aggregate_scan"


def test_summarize_repo_endpoint_rejects_invalid_mode_without_creating_job(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("print('a')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200

    srv = jsserver._init_global_ctx()
    jobs_before = set(srv.jobs.keys())

    res = client.post("/api/summarize_repo", json={"mode": "invalid-mode"})

    assert res.status_code == 400
    assert "summary mode must be 'quick' or 'full'" in res.json()["error"]
    assert set(srv.jobs.keys()) == jobs_before
