from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient


def test_get_job_detail_returns_stable_snapshot_fields() -> None:
    import codemerge.jsui.server as jsserver

    srv = jsserver._init_global_ctx()
    srv.job_service.completed_ttl_seconds = 999999999
    with srv.jobs_lock:
        srv.jobs["job-a"] = {
            "id": "job-a",
            "status": "done",
            "type": "merge",
            "progress": "done",
            "progress_pct": 100,
            "created_at": "2026-05-05T10:00:00Z",
            "completed_at": "2026-05-05T10:00:01Z",
            "result": {"output_path": "/tmp/out.txt", "token_count": 12},
        }

    client = TestClient(jsserver.app)
    res = client.get("/api/jobs/job-a")

    assert res.status_code == 200
    job = res.json()
    assert job["id"] == "job-a"
    assert job["type"] == "merge"
    assert job["status"] == "done"
    assert job["progress"] == "done"
    assert job["progress_pct"] == 100
    assert job["created_at"] == "2026-05-05T10:00:00Z"
    assert job["completed_at"] == "2026-05-05T10:00:01Z"
    assert job["result"]["token_count"] == 12
    assert job["error"] is None
    assert job["output_paths"] == ["/tmp/out.txt"]
    assert job["cancellable"] is False
    assert job["cancel_supported"] is False


def test_list_jobs_filters_and_sorts_recent_first() -> None:
    import codemerge.jsui.server as jsserver

    srv = jsserver._init_global_ctx()
    srv.job_service.completed_ttl_seconds = 999999999
    with srv.jobs_lock:
        srv.jobs["old"] = {
            "id": "old",
            "status": "done",
            "type": "scan",
            "created_at": "2026-05-05T09:00:00Z",
            "result": {"root": "/tmp/repo"},
        }
        srv.jobs["new"] = {
            "id": "new",
            "status": "running",
            "type": "summarize",
            "progress_pct": 33.4,
            "created_at": "2026-05-05T11:00:00Z",
        }

    client = TestClient(jsserver.app)

    all_jobs = client.get("/api/jobs").json()["jobs"]
    assert [job["id"] for job in all_jobs] == ["new", "old"]
    assert all_jobs[0]["progress"] == "running"
    assert all_jobs[0]["progress_pct"] == 33

    running = client.get("/api/jobs", params={"status": "running"}).json()
    assert running["count"] == 1
    assert running["jobs"][0]["id"] == "new"

    scans = client.get("/api/jobs", params={"type": "scan"}).json()
    assert scans["count"] == 1
    assert scans["jobs"][0]["id"] == "old"


def test_prune_job_snapshot_has_type_and_done_progress(monkeypatch, tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")

    def fake_prune_by_goal(goal, root_path, output_path, *, config, progress, allowed_paths):
        progress("fake prune complete")
        return {"selected": allowed_paths, "goal": goal, "root": root_path, "config": bool(config), "output": output_path}

    monkeypatch.setattr(jsserver, "_load_prune_by_goal", lambda: fake_prune_by_goal)
    client = TestClient(jsserver.app)
    res = client.post(
        "/api/prune",
        json={
            "path": str(root),
            "goal": "goal",
            "selected_paths": [str(target)],
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
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["type"] == "prune"
    assert job["status"] == "done"
    assert job["progress"] == "fake prune complete"
    assert job["progress_pct"] == 100
    assert job["output_paths"] == [res.json()["output_path"]]

    listed = client.get("/api/jobs", params={"type": "prune"}).json()
    assert listed["count"] == 1
    assert listed["jobs"][0]["id"] == job_id


def test_job_service_rejects_when_running_limit_is_reached(tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")
    srv = jsserver._init_global_ctx()
    srv.job_service.max_running = 1
    srv.job_service.max_queued = 0
    with srv.jobs_lock:
        srv.jobs["busy"] = {
            "id": "busy",
            "status": "running",
            "type": "scan",
            "created_at": "2026-05-05T10:00:00Z",
        }

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/summarize",
        json={"path": str(root), "selected_paths": [str(target)], "content_mode": "code_only"},
    )

    assert res.status_code == 429
    assert res.json()["error"] == "job queue is full"
    with srv.jobs_lock:
        assert set(srv.jobs) == {"busy"}


def test_job_service_runs_queued_job_after_capacity_frees() -> None:
    import codemerge.jsui.server as jsserver

    jobs: dict[str, dict] = {}
    lock = threading.Lock()
    service = jsserver.JobService(jobs, lock, max_running=1, max_queued=1, completed_ttl_seconds=999999999)
    release = threading.Event()
    started: list[str] = []
    started_lock = threading.Lock()

    def worker(_ctx, _job_id: str, name: str) -> None:
        with started_lock:
            started.append(name)
        if name == "first":
            assert release.wait(timeout=2)

    job_id_1, err_1 = service.submit(jsserver.JobType.SCAN, worker, (object(), "first"))
    job_id_2, err_2 = service.submit(jsserver.JobType.MERGE, worker, (object(), "second"))

    assert err_1 is None
    assert err_2 is None
    assert job_id_1 is not None
    assert job_id_2 is not None
    with lock:
        assert jobs[job_id_1]["status"] == "running"
        assert jobs[job_id_2]["status"] == "queued"
        assert jobs[job_id_2]["progress"] == "queued"
        assert jobs[job_id_2]["cancellable"] is True
        assert jobs[job_id_2]["cancel_supported"] is True

    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with lock:
            second_status = jobs[job_id_2]["status"]
        with started_lock:
            second_started = "second" in started
        if second_status == "done" and second_started:
            break
        time.sleep(0.01)

    with lock:
        assert jobs[job_id_1]["status"] == "done"
        assert jobs[job_id_2]["status"] == "done"
        assert jobs[job_id_2]["cancellable"] is False
        assert jobs[job_id_2]["cancel_supported"] is False
    with started_lock:
        assert started == ["first", "second"]


def test_cancel_endpoint_cancels_queued_job_before_launch() -> None:
    import codemerge.jsui.server as jsserver

    srv = jsserver._init_global_ctx()
    srv.job_service.max_running = 1
    srv.job_service.max_queued = 1
    srv.job_service.completed_ttl_seconds = 999999999
    release = threading.Event()
    started: list[str] = []
    started_lock = threading.Lock()

    def worker(_ctx, _job_id: str, name: str) -> None:
        with started_lock:
            started.append(name)
        if name == "first":
            assert release.wait(timeout=2)

    job_id_1, err_1 = srv.job_service.submit(jsserver.JobType.SCAN, worker, (object(), "first"))
    job_id_2, err_2 = srv.job_service.submit(jsserver.JobType.MERGE, worker, (object(), "second"))

    assert err_1 is None
    assert err_2 is None
    assert job_id_1 is not None
    assert job_id_2 is not None
    client = TestClient(jsserver.app)
    queued = client.get(f"/api/jobs/{job_id_2}").json()
    assert queued["status"] == "queued"
    assert queued["cancellable"] is True
    assert queued["cancel_supported"] is True

    cancelled = client.post(f"/api/jobs/{job_id_2}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json() == {
        "ok": True,
        "job_id": job_id_2,
        "status": "cancelled",
        "cancel_supported": True,
        "message": None,
    }
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with srv.jobs_lock:
            first_status = srv.jobs[job_id_1]["status"]
        if first_status == "done":
            break
        time.sleep(0.01)

    snapshot = client.get(f"/api/jobs/{job_id_2}").json()
    assert snapshot["status"] == "cancelled"
    assert snapshot["progress"] == "cancelled"
    assert snapshot["cancellable"] is False
    assert snapshot["cancel_supported"] is True
    with started_lock:
        assert started == ["first"]


def test_cancel_endpoint_rejects_running_non_cancellable_job() -> None:
    import codemerge.jsui.server as jsserver

    srv = jsserver._init_global_ctx()
    srv.job_service.completed_ttl_seconds = 999999999
    with srv.jobs_lock:
        srv.jobs["running"] = {
            "id": "running",
            "status": "running",
            "type": "merge",
            "progress": "writing",
            "progress_pct": 50,
            "created_at": "2026-05-06T10:00:00Z",
        }

    client = TestClient(jsserver.app)
    res = client.post("/api/jobs/running/cancel")

    assert res.status_code == 409
    assert res.json() == {
        "ok": False,
        "job_id": "running",
        "status": "running",
        "cancel_supported": False,
        "message": "running job does not support cooperative cancellation",
    }
    job = client.get("/api/jobs/running").json()
    assert job["status"] == "running"
    assert job["cancellable"] is False
    assert job["cancel_supported"] is False


def test_job_list_cleans_up_expired_terminal_jobs() -> None:
    import codemerge.jsui.server as jsserver

    srv = jsserver._init_global_ctx()
    srv.job_service.completed_ttl_seconds = 0
    with srv.jobs_lock:
        srv.jobs["old"] = {
            "id": "old",
            "status": "done",
            "type": "scan",
            "created_at": "2026-05-05T09:00:00Z",
            "completed_at": "2026-05-05T09:00:01Z",
        }
        srv.jobs["running"] = {
            "id": "running",
            "status": "running",
            "type": "merge",
            "created_at": "2026-05-05T10:00:00Z",
        }

    client = TestClient(jsserver.app)
    res = client.get("/api/jobs")

    assert res.status_code == 200
    payload = res.json()
    assert payload["count"] == 1
    assert payload["jobs"][0]["id"] == "running"
    with srv.jobs_lock:
        assert set(srv.jobs) == {"running"}
