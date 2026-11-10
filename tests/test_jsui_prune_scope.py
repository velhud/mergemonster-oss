import time
from datetime import datetime

from fastapi.testclient import TestClient


def test_jsui_config_persists_filters(tmp_path):
    import codemerge.jsui.server as jsserver

    client = TestClient(jsserver.app)

    root = tmp_path / "repo"
    root.mkdir()

    payload = {
        "last_directory": str(root),
        "last_output": str(root / "out.txt"),
        "view_mode": "hierarchy",
        "date_last_n_days": 7,
        "name_filter_glob": True,
        "content_search": "needle",
        "content_regex": True,
        "content_case_sensitive": True,
        "content_max_kb": 0,
        "ignore_binary_files": True,
        "max_file_size_mb": 1.5,
        "follow_symlinks": True,
        "max_walk_depth": 3,
        "exclude_node_modules": False,
        "use_git_files": True,
        "git_include_untracked": True,
        "git_only_changed": True,
        "git_since_ref": "main",
        "git_until_ref": "HEAD",
        "exclude_vendored": True,
        "watch_enabled": True,
        "watch_interval_sec": 5,
        "last_filters": {
            "token_min": 1,
            "token_max": 2,
            "size_min": 0.25,
            "size_max": 9.5,
            "name_pattern": r"^a\.py$",
            "date_after": "2025-01-02",
            "date_before": "",
            "categories": ["BACKEND"],
        },
        "category_mask": {"BACKEND": True, "FRONTEND": False},
    }

    res = client.put("/api/config", json=payload)
    assert res.status_code == 200
    cfg = res.json()["config"]
    assert cfg["last_directory"] == str(root)
    assert cfg["date_last_n_days"] == 7
    assert cfg["name_filter_glob"] is True
    assert cfg["content_max_kb"] == 0
    assert cfg["watch_enabled"] is True
    assert cfg["watch_interval_sec"] == 5
    assert cfg["last_filters"]["token_min"] == 1
    assert cfg["last_filters"]["token_max"] == 2
    assert cfg["last_filters"]["name_pattern"] == r"^a\.py$"
    assert float(cfg["last_filters"]["date_after"]) > 0.0
    assert cfg["category_mask"]["BACKEND"] is True
    assert cfg["category_mask"]["FRONTEND"] is False


def test_jsui_prune_empty_selection_uses_filtered_scope(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("print('A')\n", encoding="utf-8")
    (root / "b.py").write_text("print('B')\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_prune_job(ctx, job_id: str, root: str, goal: str, cfg: dict, allowed_paths: list[str] | None, output_path: str) -> None:
        captured["allowed_paths"] = allowed_paths
        with ctx.jobs_lock:
            job = ctx.jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {"output_path": output_path, "plan": {"ok": True}}
                job["completed_at"] = datetime.utcnow().isoformat() + "Z"

    monkeypatch.setattr(jsserver, "_run_prune_job", fake_run_prune_job)

    client = TestClient(jsserver.app)

    payload = {
        "path": str(root),
        "goal": "test goal",
        "selected_paths": [],
        "include_all": False,
        "ignore_custom_excluded": True,
        "always_include_docs": True,
        "exclude_node_modules": True,
        "exclude_python_noise": False,
        "exclude_java_gradle_noise": False,
        "exclude_web_build_noise": False,
        "use_git_files": False,
        "git_include_untracked": False,
        "git_only_changed": False,
        "git_since_ref": "",
        "git_until_ref": "HEAD",
        "exclude_vendored": False,
        "xcode_target": "(All)",
        "ignore_binary": False,
        "max_file_size_mb": 0.0,
        "max_walk_depth": 0,
        "follow_symlinks": False,
        "filters": {
            "name_pattern": r"a\.py$",
            "name_filter_glob": False,
            "token_min": 0,
            "token_max": 10_000_000,
            "size_min": 0.0,
            "size_max": 100_000.0,
            "date_after": "",
            "date_before": "",
            "categories": ["BACKEND", "FRONTEND", "DOCS", "CONFIG", "ASSETS", "BINARY"],
            "content_search": "",
            "content_regex": False,
            "content_case_sensitive": False,
            "content_max_kb": 256,
            "content_hide_duplicates": False,
            "content_mark_duplicates": True,
            "always_include_docs": True,
        },
        "prune_ranking_mode": "keywords",
        "prune_engine": "local",
        "prune_neighbors_mode": "none",
        "prune_mode": "compact",
        "prune_dry_run": True,
        "prune_token_budget": 1000,
        "prune_goal_llm_enabled": False,
        "prune_goal_llm_span_select": False,
        "enable_embeddings": False,
        "embedding_provider": "none",
    }

    res = client.post("/api/prune", json=payload)
    assert res.status_code == 200

    for _ in range(200):
        if "allowed_paths" in captured:
            break
        time.sleep(0.01)

    allowed = captured.get("allowed_paths")
    assert isinstance(allowed, list)
    assert any(p.endswith("a.py") for p in allowed)
    assert not any(p.endswith("b.py") for p in allowed)
