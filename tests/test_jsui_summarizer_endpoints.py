from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codemerge.models import Category, FileMeta


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        _ = timeout


class StubFileProcessor:
    def read_file_content(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def count_tokens(self, text: str) -> int:
        return max(len(str(text).split()), 1)

    def is_binary_file(self, path: str, chunk_size: int = 2048) -> bool:
        _ = path
        _ = chunk_size
        return False


def _make_meta(path: Path, root: Path) -> FileMeta:
    stat = path.stat()
    rel = path.relative_to(root)
    category = Category.DOCS if path.suffix.lower() == ".md" else Category.BACKEND
    return FileMeta(
        abs_path=path,
        rel_path=rel,
        size_bytes=stat.st_size,
        mtime=stat.st_mtime,
        token_count=max(stat.st_size, 1),
        ext=path.suffix,
        category=category,
    )


def _scan_repo(client: TestClient, root: Path) -> None:
    res = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert res.status_code == 200


def _sync_threading(monkeypatch, jsserver):
    monkeypatch.setattr(jsserver.threading, "Thread", ImmediateThread)
    srv = jsserver._init_global_ctx()
    monkeypatch.setattr(srv.summarization_manager, "run", lambda key, func, *args, **kwargs: func(*args, **kwargs))
    return srv


def _confirm_provider(client: TestClient, action: str, payload: dict) -> dict:
    res = client.post("/api/provider/preflight", json={"action": action, **payload})
    assert res.status_code == 200
    preflight = res.json()
    assert preflight["requires_confirmation"] is True
    return {
        **payload,
        "provider_confirmed": True,
        "provider_confirmation_token": preflight["confirmation_token"],
    }


def test_summarization_manager_submit_handles_immediate_completion_without_deadlock(monkeypatch):
    import concurrent.futures
    import threading

    import codemerge.jsui.server as jsserver

    manager = jsserver.SummarizationManager(max_workers=1)
    completed = concurrent.futures.Future()
    completed.set_result("ok")
    monkeypatch.setattr(manager._executor, "submit", lambda *args, **kwargs: completed)

    result_holder: dict[str, concurrent.futures.Future] = {}

    def call_submit() -> None:
        result_holder["future"] = manager.submit(("key",), lambda: "ok")

    worker = threading.Thread(target=call_submit, daemon=True)
    worker.start()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert result_holder["future"] is completed
    assert manager._inflight == {}


def _patch_router_summary_stubs(monkeypatch):
    import codemerge.summarizer.router as router

    calls: list[dict[str, object]] = []

    def _stub(label: str):
        def _run(*args, **kwargs):
            calls.append(
                {
                    "label": label,
                    "args": args,
                    "kwargs": kwargs,
                }
            )
            return f"{label} body"

        return _run

    monkeypatch.setattr(router, "ensure_folder_summary_quick", _stub("folder-quick"))
    monkeypatch.setattr(router, "ensure_folder_summary_full", _stub("folder-full"))
    monkeypatch.setattr(router, "ensure_repo_summary_quick", _stub("repo-quick"))
    monkeypatch.setattr(router, "ensure_repo_summary_full", _stub("repo-full"))
    return calls


@pytest.mark.parametrize(
    ("kind", "mode", "expected_label", "expected_prefix"),
    [
        ("folder", "quick", "folder-quick", "FOLDER SUMMARY (QUICK)"),
        ("folder", "full", "folder-full", "FOLDER SUMMARY (FULL)"),
        ("repo", "quick", "repo-quick", "REPOSITORY SUMMARY (QUICK)"),
        ("repo", "full", "repo-full", "REPOSITORY SUMMARY (FULL)"),
    ],
)
def test_summarize_endpoints_use_mocked_summarizers(monkeypatch, tmp_path, kind, mode, expected_label, expected_prefix):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    _scan_repo(client, root)
    srv = _sync_threading(monkeypatch, jsserver)
    calls = _patch_router_summary_stubs(monkeypatch)

    payload = {"mode": mode}
    if kind == "folder":
        payload["path"] = str(root)
    payload = _confirm_provider(client, f"summarize_{kind}", payload)

    res = client.post(f"/api/summarize_{kind}", json=payload)
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    job_payload = job.json()
    assert job_payload["status"] == "done"
    summary_text = job_payload["result"]["summary_text"]
    assert summary_text.startswith(expected_prefix)
    assert f"{expected_label} body" in summary_text
    assert calls and calls[0]["label"] == expected_label
    assert calls[0]["kwargs"].get("mode", mode) == mode
    assert calls[0]["kwargs"]["scope_source"] == "aggregate_scan"
    assert set(srv.jobs) == {job_id}


@pytest.mark.parametrize("kind", ["folder", "repo"])
def test_summarize_endpoints_reject_invalid_mode(monkeypatch, tmp_path, kind):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    _scan_repo(client, root)
    srv = jsserver._init_global_ctx()
    jobs_before = set(srv.jobs)

    payload = {"mode": "invalid-mode"}
    if kind == "folder":
        payload["path"] = str(root)

    res = client.post(f"/api/summarize_{kind}", json=payload)

    assert res.status_code == 400
    assert "summary mode must be 'quick' or 'full'" in res.json()["error"]
    assert set(srv.jobs) == jobs_before


def test_summarize_repo_empty_scope_sets_job_error(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()

    client = TestClient(jsserver.app)
    _scan_repo(client, root)
    srv = _sync_threading(monkeypatch, jsserver)

    payload = _confirm_provider(client, "summarize_repo", {"mode": "quick"})
    res = client.post("/api/summarize_repo", json=payload)
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    job_payload = job.json()
    assert job_payload["status"] == "error"
    assert "Repository summary scope is empty after exclusions." in job_payload["error"]
    assert set(srv.jobs) == {job_id}


def test_summarize_repo_defaults_to_full_when_mode_omitted(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    _scan_repo(client, root)
    _sync_threading(monkeypatch, jsserver)
    calls = _patch_router_summary_stubs(monkeypatch)

    payload = _confirm_provider(client, "summarize_repo", {})
    res = client.post("/api/summarize_repo", json=payload)
    assert res.status_code == 200

    assert calls and calls[0]["label"] == "repo-full"
    assert calls[0]["kwargs"].get("mode", "full") == "full"


def test_summary_job_keys_default_omitted_mode_to_full():
    import codemerge.jsui.server as jsserver

    folder_key = jsserver._folder_summary_job_key(
        "/tmp/repo/pkg",
        "/tmp/repo",
        1,
        None,
        model_name="test-model",
        runtime_fingerprint="fp-1",
    )
    repo_key = jsserver._repo_summary_job_key(
        "/tmp/repo",
        1,
        None,
        model_name="test-model",
        runtime_fingerprint="fp-1",
    )

    assert folder_key[1] == "full"
    assert repo_key[1] == "full"


def test_settings_endpoint_exposes_updated_summary_defaults(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    _scan_repo(client, root)

    res = client.get("/api/settings", params={"path": str(root)})
    assert res.status_code == 200
    payload = res.json()

    assert payload["auto_refresh_summaries"] is True
    assert payload["fallback_to_code_on_summary_error"] is False
    assert payload["summary_prompt_version"] == 7
    assert payload["groq_request_timeout_seconds"] == 120.0
    assert payload["groq_summary_retry_budget"] == 2
    assert payload["strict_summary_required"] is False


def test_settings_endpoint_accepts_timeout_and_retry_budget(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    _scan_repo(client, root)

    res = client.put(
        "/api/settings",
        json={
            "path": str(root),
            "groq_request_timeout_seconds": 9.5,
            "groq_summary_retry_budget": 4,
            "fallback_to_code_on_summary_error": False,
            "strict_summary_required": True,
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["settings"]["groq_request_timeout_seconds"] == 9.5
    assert payload["settings"]["groq_summary_retry_budget"] == 4
    assert payload["settings"]["fallback_to_code_on_summary_error"] is False
    assert payload["settings"]["strict_summary_required"] is True


def test_repo_summary_job_key_tracks_runtime_contract_changes():
    import codemerge.jsui.server as jsserver

    quick_cfg_a = {
        "summary_prompt_version": 1,
        "full_summary_model": "model-a",
        "quick_summary_max_depth": 1,
        "quick_summary_file_kind": "short",
    }
    quick_cfg_b = dict(quick_cfg_a)
    quick_cfg_b["quick_summary_max_depth"] = 3
    full_cfg = {
        "summary_prompt_version": 1,
        "full_summary_model": "model-a",
        "full_summary_file_kind": "long",
    }

    quick_contract_a = jsserver._summary_job_runtime_contract(target_kind="repo", mode="quick", cfg=quick_cfg_a)
    quick_contract_b = jsserver._summary_job_runtime_contract(target_kind="repo", mode="quick", cfg=quick_cfg_b)
    full_contract = jsserver._summary_job_runtime_contract(target_kind="repo", mode="full", cfg=full_cfg)

    quick_key_a = jsserver._repo_summary_job_key(
        "/tmp/repo",
        1,
        "quick",
        model_name=quick_contract_a[0],
        runtime_fingerprint=quick_contract_a[1],
        paths=["/tmp/repo/app.py"],
    )
    quick_key_b = jsserver._repo_summary_job_key(
        "/tmp/repo",
        1,
        "quick",
        model_name=quick_contract_b[0],
        runtime_fingerprint=quick_contract_b[1],
        paths=["/tmp/repo/app.py"],
    )
    full_key = jsserver._repo_summary_job_key(
        "/tmp/repo",
        1,
        "full",
        model_name=full_contract[0],
        runtime_fingerprint=full_contract[1],
        paths=["/tmp/repo/app.py"],
    )

    assert quick_key_a != quick_key_b
    assert quick_key_a != full_key


def test_full_coverage_proof_endpoint_uses_current_scan_manifest_when_payload_omits_it(tmp_path):
    import codemerge.jsui.server as jsserver
    from codemerge.llm.summarize import aggregate_prompt_fingerprint, resolve_full_summary_model
    from codemerge.summarizer.full import orchestrator as full_orch
    from codemerge.summarizer.shared.aggregate_cache import AggregateCache
    from codemerge.summarizer.shared.audit import serialize_audit_artifact
    from codemerge.summarizer.shared.scope import build_manifest, filter_folder_metas, inventory_in_scope

    root = tmp_path / "repo"
    root.mkdir()
    app_file = root / "app.py"
    app_file.write_text("print('hello')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    _scan_repo(client, root)
    srv = jsserver._init_global_ctx()
    cache_path = tmp_path / "cache.db"
    srv.config.update_config(
        {
            "summary_cache_db_path": str(cache_path),
            "full_summary_model": "test-model",
            "summary_prompt_version": 1,
        }
    )
    cfg = srv.config.get_config()
    aggregate_scan_files = jsserver._scan_files_for_aggregate(
        ctx=srv,
        root_abs=str(root),
        cfg=cfg,
        payload=None,
    )
    scoped_metas, _folder_prefix = filter_folder_metas(str(root), str(root), aggregate_scan_files)
    in_scope, _skipped, _errors = inventory_in_scope(metas=scoped_metas, file_processor=srv.processor)
    _rel_paths, manifest_hash = build_manifest(in_scope)
    model = resolve_full_summary_model(cfg)
    prompt_fp = aggregate_prompt_fingerprint("repo", "full", 1)

    cache = AggregateCache(str(cache_path))
    try:
        cache.put_ok(
            "repo",
            str(root),
            mode="full",
            manifest_hash=manifest_hash,
            model=model,
            prompt_fp=prompt_fp,
            summary_text="repo summary",
            file_count=1,
            skipped_count=0,
            reduction_layers=0,
            batch_count=0,
            audit_json=serialize_audit_artifact(
                {
                    "mode": "full",
                    "target_kind": "repo",
                    "target_path": str(root),
                    "pipeline_version": full_orch.FULL_PIPELINE_VERSION,
                    "runtime_fingerprint": full_orch._full_runtime_fingerprint(cfg),
                }
            ),
        )
    finally:
        cache.close()

    ok = client.post("/api/full_coverage_proof", json={"target_kind": "repo"})
    assert ok.status_code == 200
    assert ok.json()["coverage"]["manifest_hash"] == manifest_hash

    app_file.write_text("print('updated')\n", encoding="utf-8")
    _scan_repo(client, root)

    stale = client.post("/api/full_coverage_proof", json={"target_kind": "repo"})
    assert stale.status_code == 404
    assert stale.json()["error"] == "coverage proof not found"


def test_scan_files_for_aggregate_respects_custom_excluded(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    (root / "frontend").mkdir(parents=True)
    (root / "backend").mkdir(parents=True)
    (root / "frontend" / "ui.py").write_text("def render():\n    return 'ui'\n", encoding="utf-8")
    (root / "backend" / "api.py").write_text("def serve():\n    return 'api'\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    _scan_repo(client, root)
    srv = jsserver._init_global_ctx()

    all_files = jsserver._scan_files_for_aggregate(
        ctx=srv,
        root_abs=str(root),
        cfg=srv.config.get_config(),
        payload=None,
    )
    assert sorted(meta.rel_path.as_posix() for meta in all_files) == ["backend/api.py", "frontend/ui.py"]

    toggle = client.post("/api/custom_excluded/toggle", json={"path": str(root), "paths": ["frontend"]})
    assert toggle.status_code == 200

    _scan_repo(client, root)
    scoped_files = jsserver._scan_files_for_aggregate(
        ctx=srv,
        root_abs=str(root),
        cfg=srv.config.get_config(),
        payload=None,
    )
    assert [meta.rel_path.as_posix() for meta in scoped_files] == ["backend/api.py"]

def test_quick_repo_summary_falls_back_to_heuristics_when_selector_returns_nothing(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    names = [
        "main.py",
        "app.py",
        "server.py",
        "config.py",
        "index.js",
        "misc1.py",
        "misc2.py",
        "misc3.py",
        "misc4.py",
        "misc5.py",
    ]
    for name in names:
        (root / name).write_text("print('x')\n", encoding="utf-8")

    scan_files = [_make_meta(root / name, root) for name in names]
    app_config = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": "fallback-model",
        "summary_prompt_version": 1,
        "quick_summary_select_all_inventory_limit": 0,
    }
    calls: list[str] = []
    selected_paths: list[str] = []
    prompt_capture: dict[str, str] = {}

    def fake_fetch(*, prompt_text, log_context=None, **_kwargs):
        phase = str((log_context or {}).get("phase"))
        calls.append(phase)
        if phase == "selector":
            return "no picks"
        if phase == "quick":
            return "final summary"
        raise AssertionError(f"unexpected phase: {phase}")

    def fake_parse_selector_output(output, inventory):
        _ = output
        _ = inventory
        return []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root=None, strict_required=False, **_kwargs):
        _ = kind
        _ = file_processor
        _ = app_config
        _ = root
        _ = strict_required
        selected_paths.extend(file_paths)
        return ({path: f"summary for {Path(path).name}" for path in file_paths}, [])

    def fake_build_repo_summary_prompt(*args, **kwargs):
        _ = args
        prompt_capture["evidence_blocks"] = kwargs["evidence_blocks"]
        return "prompt text"

    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch)
    monkeypatch.setattr(quick_orch, "parse_selector_output", fake_parse_selector_output)
    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "build_repo_summary_prompt", fake_build_repo_summary_prompt)

    summary_text = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=scan_files,
        file_processor=StubFileProcessor(),
        app_config=app_config,
        scope_source="aggregate_scan",
    )

    picked_names = {Path(path).name for path in selected_paths}
    assert calls == ["selector", "quick"]
    assert {"main.py", "app.py", "server.py", "config.py", "index.js"}.issubset(picked_names)
    assert "summary for main.py" in prompt_capture["evidence_blocks"]
    assert "summary for app.py" in prompt_capture["evidence_blocks"]
    assert summary_text.startswith("final summary")


def test_quick_repo_summary_skips_recursive_folder_when_depth_limit_reached(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "entry.py").write_text("print('child')\n", encoding="utf-8")

    scan_files = [_make_meta(child / "entry.py", root)]
    app_config = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "groq_summary_model": "depth-model",
        "summary_prompt_version": 1,
        "quick_summary_max_depth": "0",
        "quick_summary_select_all_inventory_limit": 0,
    }
    calls: list[str] = []
    prompt_capture: dict[str, str] = {}

    def fake_fetch(*, prompt_text, log_context=None, **_kwargs):
        phase = str((log_context or {}).get("phase"))
        calls.append(phase)
        if phase == "selector":
            return "child/"
        if phase == "quick":
            return "final summary"
        raise AssertionError(f"unexpected phase: {phase}")

    def fake_parse_selector_output(output, inventory):
        _ = output
        _ = inventory
        return ["child/"]

    def fail_ensure_summaries(*_args, **_kwargs):  # pragma: no cover - should not run
        raise AssertionError("depth limit should skip recursive file summaries")

    def fake_build_repo_summary_prompt(*args, **kwargs):
        _ = args
        prompt_capture["evidence_blocks"] = kwargs["evidence_blocks"]
        return "prompt text"

    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch)
    monkeypatch.setattr(quick_orch, "parse_selector_output", fake_parse_selector_output)
    monkeypatch.setattr(quick_orch, "ensure_summaries", fail_ensure_summaries)
    monkeypatch.setattr(quick_orch, "build_repo_summary_prompt", fake_build_repo_summary_prompt)

    summary_text = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=scan_files,
        file_processor=StubFileProcessor(),
        app_config=app_config,
        scope_source="aggregate_scan",
    )

    assert calls == ["selector", "quick"]
    assert "Quick summary skipped due to depth limit." in prompt_capture["evidence_blocks"]
    assert summary_text.startswith("final summary")
