import time
from datetime import datetime

from fastapi.testclient import TestClient


def _with_provider_confirmation(client: TestClient, payload: dict) -> dict:
    res = client.post("/api/provider/preflight", json={"action": "prune", **payload})
    assert res.status_code == 200
    preflight = res.json()
    if not preflight["requires_confirmation"]:
        return payload
    return {
        **payload,
        "provider_confirmed": True,
        "provider_confirmation_token": preflight["confirmation_token"],
    }


def test_jsui_prune_merges_allowlisted_keys_with_type_coercion(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.py"
    f.write_text("print('A')\n", encoding="utf-8")

    ctx = jsserver._init_global_ctx()
    base_cfg = ctx.config.get_config().copy()

    captured: dict[str, object] = {}

    def fake_run_prune_job(ctx2, job_id: str, root: str, goal: str, cfg: dict, allowed_paths, output_path: str):
        captured["cfg"] = dict(cfg)
        with ctx2.jobs_lock:
            job = ctx2.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {"output_path": output_path, "plan": {"ok": True}}
                job["completed_at"] = datetime.utcnow().isoformat() + "Z"

    monkeypatch.setattr(jsserver, "_run_prune_job", fake_run_prune_job)

    client = TestClient(jsserver.app)
    payload = {
        "path": str(root),
        "goal": "test goal",
        "selected_paths": [str(f)],
        "prune_engine": "agent",
        "prune_token_budget": "50000",
        "llm_prefilter_top_k": "800",
        "llm_neighbor_max": 60.0,
        "prune_neighbor_max": "80",
        "prune_dry_run": "false",
        "groq_strict_mode": "true",
        "prune_goal_llm_span_select": False,
        "prune_downrank_noise": True,
        "prune_noise_dirs": "tools, docs",
        # should NOT be allowed to override
        "openai_model": "MALICIOUS",
    }
    res = client.post("/api/prune", json=_with_provider_confirmation(client, payload))
    assert res.status_code == 200

    for _ in range(200):
        if "cfg" in captured:
            break
        time.sleep(0.01)

    run_cfg = captured.get("cfg")
    assert isinstance(run_cfg, dict)
    assert run_cfg.get("prune_engine") == "agent"
    assert run_cfg.get("prune_token_budget") == 50000
    assert run_cfg.get("llm_prefilter_top_k") == 800
    assert run_cfg.get("llm_neighbor_max") == 60
    assert run_cfg.get("prune_neighbor_max") == 80
    assert run_cfg.get("prune_dry_run") is False
    assert run_cfg.get("groq_strict_mode") is True
    assert run_cfg.get("prune_goal_llm_span_select") is False
    assert run_cfg.get("prune_downrank_noise") is True
    assert run_cfg.get("prune_noise_dirs") == ["tools", "docs"]
    assert run_cfg.get("openai_model") == base_cfg.get("openai_model")


def test_jsui_prune_job_runs_loaded_prune_function(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.py"
    f.write_text("print('A')\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_prune_by_goal(goal, root_path, output_path, *, config, progress, allowed_paths):
        captured["goal"] = goal
        captured["root"] = root_path
        captured["output_path"] = output_path
        captured["allowed_paths"] = allowed_paths
        progress("fake prune complete")
        return {"selected": allowed_paths}

    monkeypatch.setattr(jsserver, "_load_prune_by_goal", lambda: fake_prune_by_goal)

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/prune",
        json={
            "path": str(root),
            "goal": "test goal",
            "selected_paths": [str(f)],
            "prune_engine": "local",
            "prune_ranking_mode": "keywords",
            "prune_neighbors_mode": "none",
            "prune_mode": "compact",
            "prune_dry_run": True,
            "prune_token_budget": 1000,
            "prune_goal_llm_enabled": False,
            "prune_goal_llm_span_select": False,
            "enable_embeddings": False,
            "embedding_provider": "none",
        },
    )
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    job = {}
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"done", "error"}:
            break
        time.sleep(0.01)

    assert job["status"] == "done"
    assert job["progress"] == "fake prune complete"
    assert job["result"]["output_path"] == res.json()["output_path"]
    assert captured["goal"] == "test goal"
    assert captured["root"] == str(root)
    assert captured["output_path"] == res.json()["output_path"]
    assert captured["allowed_paths"] == [str(f.resolve())]
