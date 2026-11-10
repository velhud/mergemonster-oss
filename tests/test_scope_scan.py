from __future__ import annotations



def test_aggregate_scope_scan_excludes_tool_cache_dirs_by_default(tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.summarizer.shared.scope_scan import scan_aggregate_scope_files

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / ".cache" / "pytest" / "v" / "cache").mkdir(parents=True)
    (root / ".ruff_cache").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    (root / ".cache" / "pytest" / "v" / "cache" / "nodeids").write_text("[]\n", encoding="utf-8")
    (root / ".ruff_cache" / "CACHEDIR.TAG").write_text("tag\n", encoding="utf-8")

    fp = FileProcessor()
    metas = scan_aggregate_scope_files(
        root_abs=str(root),
        file_processor=fp,
        app_config={},
    )
    rels = {meta.rel_path.as_posix() for meta in metas}

    assert "src/app.py" in rels
    assert ".cache/pytest/v/cache/nodeids" not in rels
    assert ".ruff_cache/CACHEDIR.TAG" not in rels


def test_aggregate_scope_scan_excludes_runtime_log_dirs_by_default(tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.summarizer.shared.scope_scan import scan_aggregate_scope_files

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    (root / "log" / "metrics.log").write_text("generated metrics\n", encoding="utf-8")
    (root / "logs" / "server.log").write_text("generated server log\n", encoding="utf-8")

    fp = FileProcessor()
    metas = scan_aggregate_scope_files(
        root_abs=str(root),
        file_processor=fp,
        app_config={},
    )
    rels = {meta.rel_path.as_posix() for meta in metas}

    assert "src/app.py" in rels
    assert "log/metrics.log" not in rels
    assert "logs/server.log" not in rels


def test_aggregate_scope_scan_allows_root_inside_absolute_excluded_dir(tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.summarizer.shared.scope_scan import scan_aggregate_scope_files

    repo = tmp_path / "repo"
    target_root = repo / "tests" / "fixtures" / "deep_nested"
    (target_root / "services" / "api").mkdir(parents=True)
    (target_root / "workers" / "jobs").mkdir(parents=True)
    (target_root / "main.py").write_text("print('x')\n", encoding="utf-8")
    (target_root / "services" / "api" / "router.py").write_text("def handle_request():\n    pass\n", encoding="utf-8")
    (target_root / "workers" / "jobs" / "runner.py").write_text("def run_jobs():\n    pass\n", encoding="utf-8")

    fp = FileProcessor()
    metas = scan_aggregate_scope_files(
        root_abs=str(target_root),
        file_processor=fp,
        app_config={"excluded_dirs": [str(repo / "tests")]},
    )
    rels = {meta.rel_path.as_posix() for meta in metas}

    assert "main.py" in rels
    assert "services/api/router.py" in rels
    assert "workers/jobs/runner.py" in rels


def test_aggregate_scope_scan_does_not_exclude_explicit_root_under_ancestor_exclusion(tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.summarizer.shared.scope_scan import scan_aggregate_scope_files

    project_root = tmp_path / "project"
    target_root = project_root / "tests" / "fixtures" / "deep_nested"
    (target_root / "services" / "api").mkdir(parents=True)
    (target_root / "workers" / "jobs").mkdir(parents=True)
    (target_root / "main.py").write_text("from services.api.router import handle\n", encoding="utf-8")
    (target_root / "services" / "api" / "router.py").write_text("def handle():\n    return 'ok'\n", encoding="utf-8")
    (target_root / "workers" / "jobs" / "runner.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    fp = FileProcessor()
    metas = scan_aggregate_scope_files(
        root_abs=str(target_root),
        file_processor=fp,
        app_config={
            "excluded_dirs": [str(project_root / "tests")],
        },
    )
    rels = {meta.rel_path.as_posix() for meta in metas}

    assert "main.py" in rels
    assert "services/api/router.py" in rels
    assert "workers/jobs/runner.py" in rels
