from __future__ import annotations
from pathlib import Path

from codemerge.models import Category, FileMeta
from codemerge.summarizer.shared.aggregate_cache import AggregateCache
from codemerge.summarizer.shared.audit import build_audit_artifact, parse_audit_artifact, serialize_audit_artifact


class DummyFP:
    def count_tokens(self, text: str) -> int:
        return max(len(str(text)), 1)

    def is_binary_file(self, _file_path: str, chunk_size: int = 2048) -> bool:
        _ = chunk_size
        return False

    def read_file_content(self, file_path: str) -> str:
        return Path(file_path).read_text(encoding="utf-8")


def _make_meta(root: Path, rel_path: str) -> FileMeta:
    abs_path = root / rel_path
    stat = abs_path.stat()
    return FileMeta(
        abs_path=abs_path,
        rel_path=Path(rel_path),
        size_bytes=stat.st_size,
        mtime=stat.st_mtime,
        token_count=1,
        ext=abs_path.suffix,
        category=Category.BACKEND,
    )


def test_quick_repo_summary_recomputes_when_inputs_hash_changes(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    file_path = root / "main.py"
    file_path.write_text("print('a')\n", encoding="utf-8")
    meta = _make_meta(root, "main.py")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    file_summary_values = iter(["FILE SUMMARY A", "FILE SUMMARY B"])
    final_calls: list[str] = []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({file_paths[0]: next(file_summary_values)}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            return "main.py"
        if phase == "quick":
            final_calls.append(phase)
            return f"quick summary #{len(final_calls)}"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    first = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )
    second = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert first == "quick summary #1"
    assert second == "quick summary #2"
    assert final_calls == ["quick", "quick"]


def test_quick_repo_summary_degrades_gracefully_when_some_selected_file_summaries_are_missing(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    main_file = root / "main.py"
    helper_file = root / "helper.py"
    main_file.write_text("print('main')\n", encoding="utf-8")
    helper_file.write_text("print('helper')\n", encoding="utf-8")
    metas = [_make_meta(root, "main.py"), _make_meta(root, "helper.py")]

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        assert len(file_paths) == 2
        return ({file_paths[0]: "FILE SUMMARY main.py"}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            return "main.py\nhelper.py"
        if phase == "quick":
            return "quick summary with partial evidence"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "quick summary with partial evidence"
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert any("selected evidence summaries unavailable: helper.py" in err for err in audit["errors"])
    assert "main.py" in audit["selected_evidence_paths"]


def test_quick_repo_summary_records_selector_fallback_in_audit(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    file_path = root / "main.py"
    file_path.write_text("print('a')\n", encoding="utf-8")
    meta = _make_meta(root, "main.py")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({file_paths[0]: "FILE SUMMARY"}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            raise RuntimeError("selector failed")
        if phase == "quick":
            return "quick fallback summary"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "quick fallback summary"
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    assert row is not None
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert audit["final_summary"] == "quick fallback summary"
    assert audit["selected_evidence_paths"] == ["main.py"]
    assert any("selector failed" in err for err in audit["errors"])


def test_quick_folder_summary_sanitizes_invalid_out_of_scope_path_mentions(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    folder = root / "codemerge"
    folder.mkdir(parents=True)
    target = folder / "router.py"
    target.write_text("print('router')\n", encoding="utf-8")
    meta = _make_meta(root, "codemerge/router.py")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({file_paths[0]: "FILE SUMMARY router.py"}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            return "router.py"
        if phase == "quick":
            return (
                "## Role in the system\n"
                "- Routes summary requests.\n\n"
                "## Uncertainties\n"
                "- README still mentions `codemerge/jsui/server.py`.\n"
            )
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_folder_summary(
        folder_abs=str(folder),
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert "codemerge/jsui/server.py" not in result
    assert "[unverified path omitted]" in result


def test_quick_repo_summary_redacts_excluded_repo_paths_even_when_they_exist(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    main_file = root / "main.py"
    excluded_file = root / "codemerge" / "jsui" / "server.py"
    excluded_file.parent.mkdir(parents=True)
    main_file.write_text("from codemerge.jsui import server\n", encoding="utf-8")
    excluded_file.write_text("def app():\n    pass\n", encoding="utf-8")
    main_meta = _make_meta(root, "main.py")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({file_paths[0]: "FILE SUMMARY main.py"}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            return "main.py"
        if phase == "quick":
            return (
                "## Where to start\n"
                "- Start at `main.py`.\n\n"
                "## Uncertainties\n"
                "- `main.py` references `codemerge/jsui/server.py`, but it is outside the current scope.\n"
            )
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[main_meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert "codemerge/jsui/server.py" not in result
    assert "[unverified path omitted]" in result


def test_quick_repo_summary_reuses_cached_selection_for_warm_cache_hit(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('a')\n", encoding="utf-8")
    (root / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    metas = [_make_meta(root, "main.py"), _make_meta(root, "config.py")]

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    selector_calls: list[str] = []
    final_calls: list[str] = []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            selector_calls.append(phase)
            return "main.py" if len(selector_calls) == 1 else "config.py"
        if phase == "quick":
            final_calls.append(phase)
            return f"quick summary #{len(final_calls)}"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    first = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )
    second = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert first.startswith("quick summary #1")
    assert second.startswith("quick summary #1")
    assert selector_calls == ["selector"]
    assert final_calls == ["quick"]


def test_quick_repo_summary_recomputes_when_unselected_file_changes(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    main_file = root / "main.py"
    config_file = root / "config.py"
    main_file.write_text("print('main')\n", encoding="utf-8")
    config_file.write_text("VALUE = 1\n", encoding="utf-8")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    selector_calls: list[str] = []
    final_calls: list[str] = []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            selector_calls.append("selector")
            return "main.py"
        if phase == "quick":
            final_calls.append("quick")
            return f"quick summary #{len(final_calls)}"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    metas_v1 = [_make_meta(root, "main.py"), _make_meta(root, "config.py")]
    first = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas_v1,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    config_file.write_text("VALUE = 2\nMORE = 3\n", encoding="utf-8")
    metas_v2 = [_make_meta(root, "main.py"), _make_meta(root, "config.py")]
    second = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas_v2,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert first.startswith("quick summary #1")
    assert second.startswith("quick summary #2")
    assert selector_calls == ["selector", "selector"]
    assert final_calls == ["quick", "quick"]


def test_quick_repo_summary_uses_top_level_cache_before_regenerating_nested_evidence(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    child = root / "app"
    child.mkdir(parents=True)
    (child / "main.py").write_text("print('a')\n", encoding="utf-8")
    metas = [_make_meta(root, "app/main.py")]

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    selector_calls: list[str] = []
    final_targets: list[str] = []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            selector_calls.append("selector")
            return "app/"
        if phase == "quick":
            final_targets.append(str(log_context.get("target_path")))
            return f"quick summary #{len(final_targets)}"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    first = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )
    second = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert first.startswith("quick summary #2")
    assert second.startswith("quick summary #2")
    assert selector_calls == ["selector", "selector"]
    assert final_targets == [str(child), str(root)]


def test_quick_repo_summary_records_partial_evidence_when_selected_file_summary_is_unavailable(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch
    from codemerge.summarizer.shared.aggregate_cache import AggregateCache

    root = tmp_path / "repo"
    root.mkdir()
    file_path = root / "main.py"
    file_path.write_text("print('x')\n", encoding="utf-8")
    meta = _make_meta(root, "main.py")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    monkeypatch.setattr(
        quick_orch,
        "ensure_summaries",
        lambda **kwargs: ({kwargs["file_paths"][0]: None}, []),
    )
    monkeypatch.setattr(
        quick_orch,
        "fetch_ai_text_summary",
        lambda *, log_context, **_kwargs: "main.py" if log_context.get("phase") == "selector" else "partial summary",
    )

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "partial summary"
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert any("selected evidence summaries unavailable: main.py" in err for err in audit["errors"])
    assert audit["selected_evidence_paths"] == ["PARTIAL"]


def test_quick_repo_summary_forces_code_folder_into_selection(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    code_dir = root / "codemerge"
    code_dir.mkdir(parents=True)
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (code_dir / "main.py").write_text("print('x')\n", encoding="utf-8")

    metas = [_make_meta(root, "README.md"), _make_meta(root, "codemerge/main.py")]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        target_path = str(log_context.get("target_path") or "")
        if phase == "selector" and target_path == str(root):
            return "README.md"
        if phase == "selector" and target_path.endswith("/codemerge"):
            return "main.py"
        if phase == "quick":
            return "quick summary"
        raise AssertionError(f"unexpected phase={phase} target_path={target_path}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "quick summary"
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    assert row is not None
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert "codemerge/" in audit["selected_evidence_paths"]


def test_quick_repo_summary_invalidates_cached_row_without_pipeline_version(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('a')\n", encoding="utf-8")
    meta = _make_meta(root, "main.py")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }
    manifest_hash = quick_orch.build_manifest([meta])[1]
    inventory_hash = quick_orch.compute_inventory_hash(["main.py"])
    inputs_hash = quick_orch.compute_inputs_hash([("main.py", "FILE SUMMARY")])

    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        stale_audit = build_audit_artifact(
            mode="quick",
            target_kind="repo",
            target_path=str(root),
            scope_source="test",
            manifest_hash=manifest_hash,
            inputs_hash=inputs_hash,
            inventory_hash=inventory_hash,
            runtime_fingerprint=None,
            file_count=1,
            skipped_count=0,
            inventory_count=1,
            selected_evidence_paths=["main.py"],
            candidate_summary="stale summary",
            final_summary="stale summary",
            reduction_layers=0,
            group_counts=[],
            provider_calls={"aggregate_total": 0},
            cache={"hit": True},
            timings={"total_ms": 1},
            errors=[],
        )
        cache.put_ok(
            kind="repo",
            path=str(root),
            mode="quick",
            manifest_hash=manifest_hash,
            inputs_hash=inputs_hash,
            inventory_hash=inventory_hash,
            model="test-model",
            prompt_fp=quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1),
            summary_text="stale summary",
            file_count=1,
            skipped_count=0,
            reduction_layers=0,
            batch_count=0,
            group_counts=None,
            inventory_count=1,
            audit_json=serialize_audit_artifact(stale_audit),
        )
    finally:
        cache.close()

    calls: list[str] = []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({file_paths[0]: "FILE SUMMARY"}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        calls.append(str(phase))
        if phase == "selector":
            return "main.py"
        if phase == "quick":
            return "fresh summary"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "fresh summary"
    assert calls == ["selector", "quick"]
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert audit["pipeline_version"] == quick_orch.QUICK_PIPELINE_VERSION


def test_quick_repo_summary_invalidates_cached_row_from_older_pipeline(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('a')\n", encoding="utf-8")
    meta = _make_meta(root, "main.py")

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }
    manifest_hash = quick_orch.build_manifest([meta])[1]
    inventory_hash = quick_orch.compute_inventory_hash(["main.py"])
    inputs_hash = quick_orch.compute_inputs_hash([("main.py", "FILE SUMMARY")])

    stale_audit = build_audit_artifact(
        mode="quick",
        target_kind="repo",
        target_path=str(root),
        scope_source="test",
        manifest_hash=manifest_hash,
        inputs_hash=inputs_hash,
        inventory_hash=inventory_hash,
        runtime_fingerprint=quick_orch._quick_runtime_fingerprint("repo", cfg),
        file_count=1,
        skipped_count=0,
        inventory_count=1,
        selected_evidence_paths=["main.py"],
        pipeline_version=max(0, quick_orch.QUICK_PIPELINE_VERSION - 1),
        candidate_summary="stale summary",
        final_summary="stale summary",
        reduction_layers=0,
        group_counts=[],
        provider_calls={"aggregate_total": 0},
        cache={"hit": True},
        timings={"total_ms": 1},
        errors=[],
    )

    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        cache.put_ok(
            kind="repo",
            path=str(root),
            mode="quick",
            manifest_hash=manifest_hash,
            inputs_hash=inputs_hash,
            inventory_hash=inventory_hash,
            model="test-model",
            prompt_fp=quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1),
            summary_text="stale summary",
            file_count=1,
            skipped_count=0,
            reduction_layers=0,
            batch_count=0,
            group_counts=None,
            inventory_count=1,
            audit_json=serialize_audit_artifact(stale_audit),
        )
    finally:
        cache.close()

    calls: list[str] = []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({file_paths[0]: "FILE SUMMARY"}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        calls.append(str(phase))
        if phase == "selector":
            return "main.py"
        if phase == "quick":
            return "fresh summary"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "fresh summary"
    assert calls == ["selector", "quick"]


def test_quick_repo_summary_auto_selects_small_visible_inventory_without_selector(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    code_dir = root / "codemerge"
    hidden_dir = root / ".cache"
    code_dir.mkdir(parents=True)
    hidden_dir.mkdir(parents=True)
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (code_dir / "main.py").write_text("print('x')\n", encoding="utf-8")
    (hidden_dir / "note.py").write_text("print('hidden')\n", encoding="utf-8")

    metas = [
        _make_meta(root, "README.md"),
        _make_meta(root, "codemerge/main.py"),
        _make_meta(root, ".cache/note.py"),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 10,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            raise AssertionError("selector should not run for small inventory auto-picks")
        if phase == "quick":
            return "quick summary"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "quick summary"
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert "README.md" in audit["selected_evidence_paths"]
    assert "codemerge/" in audit["selected_evidence_paths"]
    assert ".cache/" not in audit["selected_evidence_paths"]


def test_quick_repo_summary_invalidates_cached_row_when_runtime_fingerprint_changes(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('a')\n", encoding="utf-8")
    meta = _make_meta(root, "main.py")

    stale_cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
        "quick_summary_max_depth": 1,
    }
    fresh_cfg = dict(stale_cfg)
    fresh_cfg["quick_summary_max_depth"] = 3
    manifest_hash = quick_orch.build_manifest([meta])[1]
    inventory_hash = quick_orch.compute_inventory_hash(["main.py"])
    inputs_hash = quick_orch.compute_inputs_hash([("main.py", "FILE SUMMARY")])

    stale_audit = build_audit_artifact(
        mode="quick",
        target_kind="repo",
        target_path=str(root),
        scope_source="test",
        manifest_hash=manifest_hash,
        inputs_hash=inputs_hash,
        inventory_hash=inventory_hash,
        runtime_fingerprint=quick_orch._quick_runtime_fingerprint("repo", stale_cfg),
        file_count=1,
        skipped_count=0,
        inventory_count=1,
        selected_evidence_paths=["main.py"],
        pipeline_version=quick_orch.QUICK_PIPELINE_VERSION,
        candidate_summary="stale summary",
        final_summary="stale summary",
        reduction_layers=0,
        group_counts=[],
        provider_calls={"aggregate_total": 0},
        cache={"hit": True},
        timings={"total_ms": 1},
        errors=[],
    )

    cache = AggregateCache(stale_cfg["summary_cache_db_path"])
    try:
        cache.put_ok(
            kind="repo",
            path=str(root),
            mode="quick",
            manifest_hash=manifest_hash,
            inputs_hash=inputs_hash,
            inventory_hash=inventory_hash,
            model="test-model",
            prompt_fp=quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1),
            summary_text="stale summary",
            file_count=1,
            skipped_count=0,
            reduction_layers=0,
            batch_count=0,
            group_counts=None,
            inventory_count=1,
            audit_json=serialize_audit_artifact(stale_audit),
        )
    finally:
        cache.close()

    calls: list[str] = []

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({file_paths[0]: "FILE SUMMARY"}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        calls.append(str(phase))
        if phase == "selector":
            return "main.py"
        if phase == "quick":
            return "fresh summary"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=DummyFP(),
        app_config=fresh_cfg,
    )

    assert result == "fresh summary"
    assert calls == ["selector", "quick"]


def test_quick_folder_summary_adds_signal_files_when_selector_is_too_narrow(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    folder = root / "shared"
    folder.mkdir(parents=True)
    for name in ["aggregate_cache.py", "audit.py", "scope.py", "notes.md"]:
        (folder / name).write_text(f"# {name}\n", encoding="utf-8")

    metas = [_make_meta(root, f"shared/{name}") for name in ["aggregate_cache.py", "audit.py", "scope.py", "notes.md"]]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "selector":
            return "aggregate_cache.py\nscope.py"
        if phase == "quick":
            return "quick folder summary"
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_folder_summary(
        folder_abs=str(folder),
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "quick folder summary"
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("folder", str(folder), "test-model", quick_orch.aggregate_prompt_fingerprint("folder", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert "audit.py" in audit["selected_evidence_paths"]


def test_quick_folder_summary_preserves_major_child_folder_breadth(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    folder = root / "codemerge"
    for child in ["agent", "core", "jsui", "llm", "summarizer", "prune"]:
        child_dir = folder / child
        child_dir.mkdir(parents=True)
        (child_dir / "main.py").write_text(f"print('{child}')\n", encoding="utf-8")

    metas = [_make_meta(root, f"codemerge/{child}/main.py") for child in ["agent", "core", "jsui", "llm", "summarizer", "prune"]]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        target_path = str(log_context.get("target_path") or "")
        if phase == "selector" and target_path == str(folder):
            return "agent/\ncore/\nllm/"
        if phase == "selector":
            return "main.py"
        if phase == "quick":
            return f"quick summary for {Path(target_path).name or 'repo'}"
        raise AssertionError(f"unexpected phase={phase} target_path={target_path}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_folder_summary(
        folder_abs=str(folder),
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result == "quick summary for codemerge"
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("folder", str(folder), "test-model", quick_orch.aggregate_prompt_fingerprint("folder", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    folders = [item for item in audit["selected_evidence_paths"] if item.endswith("/")]
    assert len(folders) >= 4
    assert "jsui/" in folders
    assert "summarizer/" in folders


def test_quick_folder_summary_keeps_top_ranked_folder_even_when_selector_already_picked_enough(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    folder = root / "codemerge"
    for child in ["agent", "core", "filemerger", "jsui", "llm", "summarizer"]:
        child_dir = folder / child
        child_dir.mkdir(parents=True)
        (child_dir / "main.py").write_text(f"print('{child}')\n", encoding="utf-8")

    metas = [
        _make_meta(root, f"codemerge/{child}/main.py")
        for child in ["agent", "core", "filemerger", "jsui", "llm", "summarizer"]
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        target_path = str(log_context.get("target_path") or "")
        if phase == "selector" and target_path == str(folder):
            return "agent/\ncore/\nfilemerger/\nllm/\nsummarizer/"
        if phase == "selector":
            return "main.py"
        if phase == "quick":
            return f"quick summary for {Path(target_path).name or 'repo'}"
        raise AssertionError(f"unexpected phase={phase} target_path={target_path}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    quick_orch.ensure_folder_summary(
        folder_abs=str(folder),
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("folder", str(folder), "test-model", quick_orch.aggregate_prompt_fingerprint("folder", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    folders = [item for item in audit["selected_evidence_paths"] if item.endswith("/")]
    assert "jsui/" in folders
    assert "filemerger/" in folders


def test_quick_folder_summary_normalizes_root_scope_and_module_alias_paths(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "fixture"
    (root / "services" / "api").mkdir(parents=True)
    (root / "workers" / "jobs").mkdir(parents=True)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    (root / "main.py").write_text(
        "from services.api.router import handle_request\nfrom workers.jobs.runner import run_jobs\n",
        encoding="utf-8",
    )
    (root / "services" / "api" / "router.py").write_text("def handle_request(value):\n    return value\n", encoding="utf-8")
    (root / "workers" / "jobs" / "runner.py").write_text("def run_jobs():\n    return []\n", encoding="utf-8")

    metas = [
        _make_meta(root, "README.md"),
        _make_meta(root, "main.py"),
        _make_meta(root, "services/api/router.py"),
        _make_meta(root, "workers/jobs/runner.py"),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 10,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        if phase == "quick":
            return (
                "# `//`\n"
                "## Likely entry points / interfaces\n"
                "- `//main.py`\n"
                "- `services.api.router`\n"
                "- `workers.jobs.runner`\n"
                "## Where to change behavior first\n"
                "- To change API routing, start at `services.api.router` because it handles request wiring.\n"
                "- To change job execution, start at `workers.jobs.runner` because it runs background jobs.\n"
            )
        raise AssertionError(f"unexpected phase: {phase}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    summary = quick_orch.ensure_folder_summary(
        folder_abs=str(root),
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert "# `/`" in summary
    assert "`main.py`" in summary
    assert "`services/api/router.py`" in summary
    assert "`workers/jobs/runner.py`" in summary
    assert "//main.py" not in summary
    assert "services.api.router" not in summary
    assert "workers.jobs.runner" not in summary


def test_quick_repo_summary_adds_nested_signal_files_from_selected_folder(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('main')\n", encoding="utf-8")
    (root / "codemerge" / "jsui").mkdir(parents=True)
    (root / "codemerge" / "filemerger" / "services").mkdir(parents=True)
    (root / "codemerge" / "summarizer").mkdir(parents=True)
    (root / "codemerge" / "jsui" / "server.py").write_text("app = 'server'\n", encoding="utf-8")
    (root / "codemerge" / "filemerger" / "services" / "merge_service.py").write_text("def merge_preview():\n    return 1\n", encoding="utf-8")
    (root / "codemerge" / "summarizer" / "router.py").write_text("def route():\n    return 1\n", encoding="utf-8")

    metas = [
        _make_meta(root, "main.py"),
        _make_meta(root, "codemerge/jsui/server.py"),
        _make_meta(root, "codemerge/filemerger/services/merge_service.py"),
        _make_meta(root, "codemerge/summarizer/router.py"),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        target_path = str(log_context.get("target_path") or "")
        if phase == "selector" and target_path == str(root):
            return "codemerge/"
        if phase == "selector" and target_path.endswith("/codemerge"):
            return "jsui/\nfilemerger/\nsummarizer/"
        if phase == "selector":
            return "server.py\nmerge_service.py\nrouter.py"
        if phase == "quick":
            return f"quick summary for {Path(target_path).name or 'repo'}"
        raise AssertionError(f"unexpected phase={phase} target_path={target_path}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result.startswith("quick summary for repo")
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    selected_paths = set(audit["selected_evidence_paths"])
    assert "codemerge/jsui/server.py" in selected_paths
    assert "codemerge/filemerger/services/merge_service.py" in selected_paths
    assert "codemerge/summarizer/router.py" in selected_paths


def test_quick_repo_summary_prefers_runtime_nested_signals_over_config_noise(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('main')\n", encoding="utf-8")
    (root / "preferences").mkdir()
    (root / "preferences" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "preferences" / "settings.json.bak").write_text("{}", encoding="utf-8")
    (root / "codemerge" / "docs").mkdir(parents=True)
    (root / "codemerge" / "docs" / "analysis-fixtures.json").write_text("{}", encoding="utf-8")
    (root / "codemerge" / "filemerger" / "services").mkdir(parents=True)
    (root / "codemerge" / "summarizer").mkdir(parents=True)
    (root / "codemerge" / "filemerger" / "services" / "merge_service.py").write_text("def merge_preview():\n    return 1\n", encoding="utf-8")
    (root / "codemerge" / "summarizer" / "router.py").write_text("def route():\n    return 1\n", encoding="utf-8")

    metas = [
        _make_meta(root, "main.py"),
        _make_meta(root, "preferences/settings.json"),
        _make_meta(root, "preferences/settings.json.bak"),
        _make_meta(root, "codemerge/docs/analysis-fixtures.json"),
        _make_meta(root, "codemerge/filemerger/services/merge_service.py"),
        _make_meta(root, "codemerge/summarizer/router.py"),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 0,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        target_path = str(log_context.get("target_path") or "")
        if phase == "selector" and target_path == str(root):
            return "preferences/\ncodemerge/"
        if phase == "selector":
            return ""
        if phase == "quick":
            return f"quick summary for {Path(target_path).name or 'repo'}"
        raise AssertionError(f"unexpected phase={phase} target_path={target_path}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_repo_summary(
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert result.startswith("quick summary for repo")
    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get("repo", str(root), "test-model", quick_orch.aggregate_prompt_fingerprint("repo", "quick", 1), mode="quick")
    finally:
        cache.close()
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    selected_paths = set(audit["selected_evidence_paths"])
    assert "codemerge/filemerger/services/merge_service.py" in selected_paths
    assert "codemerge/summarizer/router.py" in selected_paths


def test_quick_folder_summary_roots_nested_scope_paths(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    folder = root / "codemerge" / "filemerger"
    (folder / "services").mkdir(parents=True)
    (folder / "api.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (folder / "config.py").write_text("DEFAULT = 1\n", encoding="utf-8")
    (folder / "services" / "merge_service.py").write_text("def preview():\n    pass\n", encoding="utf-8")

    metas = [
        _make_meta(root, "codemerge/filemerger/api.py"),
        _make_meta(root, "codemerge/filemerger/config.py"),
        _make_meta(root, "codemerge/filemerger/services/merge_service.py"),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 12,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        target_path = str(log_context.get("target_path") or "")
        if phase == "quick" and target_path == str(folder / "services"):
            return "merge flow lives in merge_service.py"
        if phase == "quick" and target_path == str(folder):
            return (
                "Start at api.py. "
                "To change config defaults, start at config.py. "
                "Merge flow lives in merge_service.py."
            )
        raise AssertionError(f"unexpected phase={phase} target_path={target_path}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_folder_summary(
        folder_abs=str(folder),
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert "codemerge/filemerger/api.py" in result
    assert "codemerge/filemerger/config.py" in result
    assert "codemerge/filemerger/services/merge_service.py" in result


def test_quick_folder_summary_normalizes_module_style_paths(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo" / "deep_nested"
    (root / "services" / "api").mkdir(parents=True)
    (root / "workers" / "jobs").mkdir(parents=True)
    (root / "README.md").write_text("# Deep nested fixture\n", encoding="utf-8")
    (root / "main.py").write_text(
        "from services.api.router import handle_request\n"
        "from workers.jobs.runner import run_jobs\n",
        encoding="utf-8",
    )
    (root / "services" / "api" / "router.py").write_text("def handle_request():\n    pass\n", encoding="utf-8")
    (root / "workers" / "jobs" / "runner.py").write_text("def run_jobs():\n    pass\n", encoding="utf-8")

    metas = [
        _make_meta(root, "README.md"),
        _make_meta(root, "main.py"),
        _make_meta(root, "services/api/router.py"),
        _make_meta(root, "workers/jobs/runner.py"),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 12,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        phase = log_context.get("phase")
        target_path = str(log_context.get("target_path") or "")
        if phase == "quick" and target_path == str(root):
            return "main.py calls services.api.router and workers.jobs.runner."
        if phase == "quick":
            return f"quick summary for {Path(target_path).name}"
        raise AssertionError(f"unexpected phase={phase} target_path={target_path}")

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = quick_orch.ensure_folder_summary(
        folder_abs=str(root),
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    assert "services/api/router.py" in result
    assert "workers/jobs/runner.py" in result


def test_quick_nested_folder_summary_does_not_overwrite_top_level_cache(monkeypatch, tmp_path):
    from codemerge.summarizer.quick import orchestrator as quick_orch

    root = tmp_path / "repo"
    folder = root / "codemerge" / "jsui"
    static_dir = folder / "static"
    static_dir.mkdir(parents=True)
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "__main__.py").write_text("from .server import app\n", encoding="utf-8")
    (folder / "server.py").write_text("app = object()\n", encoding="utf-8")
    (static_dir / "app.js").write_text("window.app = true;\n", encoding="utf-8")

    metas = [
        _make_meta(root, "codemerge/jsui/__init__.py"),
        _make_meta(root, "codemerge/jsui/__main__.py"),
        _make_meta(root, "codemerge/jsui/server.py"),
        _make_meta(root, "codemerge/jsui/static/app.js"),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
        "quick_summary_select_all_inventory_limit": 10,
        "quick_summary_max_depth": 2,
    }

    def fake_ensure_summaries(*, file_paths, kind, file_processor, app_config, root, strict_required):
        _ = (kind, file_processor, app_config, root, strict_required)
        return ({path: f"FILE SUMMARY {Path(path).name}" for path in file_paths}, [])

    def fake_fetch_ai_text_summary(*, prompt_text, app_config, token_counter, log_context, **_kwargs):
        _ = (prompt_text, app_config, token_counter)
        if log_context.get("phase") != "quick":
            raise AssertionError(f"unexpected phase: {log_context.get('phase')}")
        return f"quick summary for {Path(str(log_context.get('target_path') or '')).name}"

    monkeypatch.setattr(quick_orch, "ensure_summaries", fake_ensure_summaries)
    monkeypatch.setattr(quick_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    top_level = quick_orch.ensure_folder_summary(
        folder_abs=str(folder),
        root_abs=str(root),
        scan_files=metas,
        file_processor=DummyFP(),
        app_config=cfg,
    )

    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        nested = quick_orch._summarize_quick(
            target_kind="folder",
            target_path=str(folder),
            root_abs=str(root),
            scan_files=metas,
            file_processor=DummyFP(),
            app_config=cfg,
            depth=2,
            cache=cache,
            scope_source="test",
        )
        row = cache.get(
            "folder",
            str(folder),
            "test-model",
            quick_orch.aggregate_prompt_fingerprint("folder", "quick", 1),
            mode="quick",
        )
    finally:
        cache.close()

    assert top_level.startswith("quick summary for jsui")
    assert nested.startswith("quick summary for jsui")
    assert row is not None
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit is not None
    assert set(audit["selected_evidence_paths"]) == {
        "__init__.py",
        "__main__.py",
        "server.py",
        "static/",
        "static/app.js",
    }
