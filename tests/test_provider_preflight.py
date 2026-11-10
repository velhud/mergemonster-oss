from __future__ import annotations

from fastapi.testclient import TestClient


def test_summarize_provider_preflight_blocks_job_until_confirmed(tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    srv = jsserver._init_global_ctx()

    payload = {
        "path": str(root),
        "selected_paths": [str(target)],
        "content_mode": "code_plus_short",
    }
    preflight = client.post("/api/provider/preflight", json={"action": "summarize", **payload})

    assert preflight.status_code == 200
    preflight_payload = preflight.json()
    assert preflight_payload["requires_confirmation"] is True
    assert preflight_payload["confirmation_token"]
    assert preflight_payload["budget"]["estimated_files"] == 1
    assert preflight_payload["budget"]["estimated_provider_calls"] == 1

    blocked = client.post("/api/summarize", json=payload)

    assert blocked.status_code == 428
    assert blocked.json()["preflight"]["confirmation_token"] == preflight_payload["confirmation_token"]
    assert srv.jobs == {}


def test_code_only_summarize_does_not_require_provider_confirmation(tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    payload = {
        "path": str(root),
        "selected_paths": [str(target)],
        "content_mode": "code_only",
    }
    preflight = client.post("/api/provider/preflight", json={"action": "summarize", **payload})

    assert preflight.status_code == 200
    assert preflight.json()["requires_confirmation"] is False

    res = client.post("/api/summarize", json=payload)

    assert res.status_code == 200
    job = client.get(f"/api/jobs/{res.json()['job_id']}").json()
    assert job["type"] == "summarize"


def test_skeleton_only_summarize_does_not_require_or_run_provider(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("def a():\n    return 1\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    srv = jsserver._init_global_ctx()

    calls: list[tuple] = []

    def fail_provider(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("skeleton-only summarize must not call ensure_summaries")

    monkeypatch.setattr(jsserver, "ensure_summaries", fail_provider)

    payload = {
        "path": str(root),
        "selected_paths": [str(target)],
        "content_mode": "skeleton_only",
    }
    preflight = client.post("/api/provider/preflight", json={"action": "summarize", **payload})
    assert preflight.status_code == 200
    assert preflight.json()["requires_confirmation"] is False

    res = client.post("/api/summarize", json=payload)
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    for _ in range(20):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            break
    assert job["status"] == "done"
    assert job["result"]["summarized"] == 0
    assert calls == []
    assert srv.jobs[job_id]["status"] == "done"


def test_pair_ai_confirmation_token_allows_confirmed_local_stub(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver
    from codemerge.llm.accounting import record_provider_call

    root = tmp_path / "repo"
    src_a = root / "a"
    src_b = root / "b"
    src_a.mkdir(parents=True)
    src_b.mkdir()

    client = TestClient(jsserver.app)
    srv = jsserver._init_global_ctx()
    srv.config.update_config({"last_directory": str(root)})

    called: list[tuple[str, str]] = []

    def fake_compare(a: str, b: str) -> dict[str, str]:
        called.append((a, b))
        record_provider_call(provider="groq", operation="responses.create", model="pair-test")
        return {"same.py": "same"}

    monkeypatch.setattr(srv.processor, "compare_codebases_ai", fake_compare)
    monkeypatch.setattr(srv.processor, "format_comparison_report", lambda results, **_kwargs: str(results))

    payload = {"src_a": str(src_a), "src_b": str(src_b)}
    blocked = client.post("/api/pair_ai", json=payload)
    assert blocked.status_code == 428
    assert called == []

    preflight = client.post("/api/provider/preflight", json={"action": "pair_ai", **payload}).json()
    confirmed = {
        **payload,
        "provider_confirmed": True,
        "provider_confirmation_token": preflight["confirmation_token"],
    }
    res = client.post("/api/pair_ai", json=confirmed)

    assert res.status_code == 200
    assert called == [(str(src_a), str(src_b))]
    assert res.json()["provider_accounting"]["estimated_provider_calls"] == 1
    assert res.json()["provider_accounting"]["observed_provider_calls"] == 1
    assert res.json()["provider_accounting"]["observed_by_operation"] == {"responses.create": 1}


def test_provider_risk_job_snapshot_records_preflight_metadata(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver
    from codemerge.llm.accounting import record_provider_call

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")

    def fake_run_prune_job(ctx, job_id: str, root_path: str, goal: str, cfg: dict, allowed_paths, output_path: str) -> None:
        record_provider_call(provider="groq", operation="responses.create", model="prune-test")
        record_provider_call(provider="openai", operation="embeddings.create", model="embed-test")
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {"output_path": output_path, "plan": {"goal": goal, "root": root_path}}
                job["progress_pct"] = 100
                job["completed_at"] = jsserver._utc_now_iso()

    monkeypatch.setattr(jsserver, "_run_prune_job", fake_run_prune_job)

    client = TestClient(jsserver.app)
    payload = {
        "path": str(root),
        "goal": "goal",
        "selected_paths": [str(target)],
        "prune_engine": "local",
        "prune_goal_llm_enabled": True,
        "prune_goal_llm_span_select": False,
        "enable_embeddings": False,
    }
    preflight = client.post("/api/provider/preflight", json={"action": "prune", **payload}).json()
    res = client.post(
        "/api/prune",
        json={
            **payload,
            "provider_confirmed": True,
            "provider_confirmation_token": preflight["confirmation_token"],
        },
    )

    assert res.status_code == 200
    job = client.get(f"/api/jobs/{res.json()['job_id']}").json()
    assert job["provider_preflight"]["action"] == "prune"
    assert job["provider_accounting"]["estimated_provider_calls"] == 1
    assert job["provider_accounting"]["observed_provider_calls"] == 2
    assert job["provider_accounting"]["observed_by_provider"] == {"groq": 1, "openai": 1}
    assert job["provider_accounting"]["observed_by_model"] == {"embed-test": 1, "prune-test": 1}
