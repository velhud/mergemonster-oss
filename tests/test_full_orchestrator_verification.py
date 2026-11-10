from __future__ import annotations

from pathlib import Path

from codemerge.models import Category, FileMeta


def _make_meta(path: Path, root: Path) -> FileMeta:
    stat = path.stat()
    return FileMeta(
        abs_path=path,
        rel_path=path.relative_to(root),
        size_bytes=stat.st_size,
        mtime=stat.st_mtime,
        token_count=max(stat.st_size, 1),
        ext=path.suffix,
        category=Category.BACKEND,
    )


def _valid_repo_summary(*paths: str) -> str:
    path_lines = "\n".join(f"- `{path}`" for path in paths)
    return (
        "# Repository: `demo`\n"
        "## System overview\n"
        f"{path_lines}\n"
        "## Major subsystems\n"
        f"- Runtime areas: {', '.join(f'`{path}`' for path in paths)}\n"
        "## Entry points / how it runs\n"
        f"{path_lines}\n"
        "## Cross-subsystem flows\n"
        f"- Flow touches {', '.join(f'`{path}`' for path in paths)}.\n"
        "## Dependencies / infrastructure\n"
        "- External services remain generic.\n"
        "## Side effects map\n"
        "- filesystem\n"
        "## Where to change X\n"
        f"{path_lines}\n"
        "## Uncertainties\n"
        "- None.\n"
        "## Coverage note\n"
        "- Complete.\n"
    )


def _valid_folder_summary(*paths: str) -> str:
    path_lines = "\n".join(f"- `{path}`" for path in paths)
    return (
        "# Folder summary\n"
        "## Role in the system\n"
        f"{path_lines}\n"
        "## Structure map\n"
        f"- Coverage: {', '.join(f'`{path}`' for path in paths)}\n"
        "## Interfaces / entry points\n"
        f"{path_lines}\n"
        "## Key flows through this area\n"
        f"- Flow touches {', '.join(f'`{path}`' for path in paths)}.\n"
        "## Dependencies\n"
        "- External services remain generic.\n"
        "## Side effects zones\n"
        "- filesystem\n"
        "## Where to change X\n"
        f"{path_lines}\n"
        "## Uncertainties\n"
        "- None.\n"
        "## Coverage note\n"
        "- Complete.\n"
    )


def test_full_summary_reuses_cached_summary_when_runtime_contract_matches(monkeypatch, tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.llm.summarize import aggregate_prompt_fingerprint, resolve_full_summary_model
    from codemerge.summarizer.full import orchestrator as full_orch
    from codemerge.summarizer.shared.aggregate_cache import AggregateCache
    from codemerge.summarizer.shared.audit import serialize_audit_artifact

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")
    meta = _make_meta(target, root)

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
    }

    fp = FileProcessor()
    file_summary = "file summary"
    scoped_metas, folder_prefix = full_orch.filter_folder_metas(str(root), str(root), [meta])
    in_scope, _skipped, _errors = full_orch.inventory_in_scope(metas=scoped_metas, file_processor=fp)
    _rel_paths, manifest_hash = full_orch.build_manifest(in_scope)
    direct_metas, children = full_orch.split_direct_vs_descendant(in_scope, folder_prefix)
    assert not children
    evidence_items = []
    for direct_meta in direct_metas:
        rel_local = full_orch.folder_rel_path(full_orch.posix(direct_meta.rel_path.as_posix()), folder_prefix)
        evidence_items.append((rel_local, file_summary))

    inputs_hash = full_orch.compute_inputs_hash(evidence_items)
    inventory_hash = full_orch.compute_inventory_hash(["a.py"])
    model = resolve_full_summary_model(cfg)
    prompt_fp = aggregate_prompt_fingerprint("repo", "full", 1)

    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        cache.put_ok(
            kind="repo",
            path=str(root),
            mode="full",
            manifest_hash=manifest_hash,
            inputs_hash=inputs_hash,
            inventory_hash=inventory_hash,
            model=model,
            prompt_fp=prompt_fp,
            summary_text="cached summary",
            file_count=1,
            skipped_count=0,
            reduction_layers=0,
            batch_count=0,
            group_counts=None,
            inventory_count=1,
            scope_label=full_orch.SCOPE_LABEL,
            scope_definition_version=full_orch.SCOPE_DEFINITION_VERSION,
            audit_json=serialize_audit_artifact(
                {
                    "pipeline_version": full_orch.FULL_PIPELINE_VERSION,
                    "runtime_fingerprint": full_orch._full_runtime_fingerprint(cfg),
                }
            ),
        )
    finally:
        cache.close()

    monkeypatch.setattr(
        full_orch,
        "ensure_summaries",
        lambda **_kwargs: ({str(target): file_summary}, []),
    )
    monkeypatch.setattr(
        full_orch,
        "fetch_ai_text_summary",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cached summary should be reused")),
    )

    result = full_orch.ensure_repo_summary_full(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=fp,
        app_config=cfg,
    )

    assert result == "cached summary"


def test_full_summary_injects_missing_inventory_change_pointers(monkeypatch, tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.summarizer.full import orchestrator as full_orch

    root = tmp_path / "repo"
    prompts = root / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "protocol.md").write_text("placeholder docs\n", encoding="utf-8")
    (prompts / "repo_quick_summary.md").write_text("quick prompt\n", encoding="utf-8")

    metas = [
        _make_meta(prompts / "protocol.md", root),
        _make_meta(prompts / "repo_quick_summary.md", root),
    ]
    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
    }

    monkeypatch.setattr(
        full_orch,
        "ensure_summaries",
        lambda **_kwargs: (
            {
                str(prompts / "protocol.md"): "evidence file summary",
                str(prompts / "repo_quick_summary.md"): "repo quick prompt summary",
            },
            [],
        ),
    )
    monkeypatch.setattr(
        full_orch,
        "fetch_ai_text_summary",
        lambda **_kwargs: _valid_folder_summary("repo_quick_summary.md"),
    )

    result = full_orch.ensure_folder_summary_full(
        folder_abs=str(prompts),
        root_abs=str(root),
        scan_files=metas,
        file_processor=FileProcessor(),
        app_config=cfg,
    )

    assert "`protocol.md`" in result


def test_full_summary_recomputes_when_runtime_contract_mismatches(monkeypatch, tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.llm.summarize import aggregate_prompt_fingerprint, resolve_full_summary_model
    from codemerge.summarizer.full import orchestrator as full_orch
    from codemerge.summarizer.shared.aggregate_cache import AggregateCache
    from codemerge.summarizer.shared.audit import serialize_audit_artifact, parse_audit_artifact

    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")
    meta = _make_meta(target, root)

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
    }

    fp = FileProcessor()
    file_summary = "file summary"
    scoped_metas, folder_prefix = full_orch.filter_folder_metas(str(root), str(root), [meta])
    in_scope, _skipped, _errors = full_orch.inventory_in_scope(metas=scoped_metas, file_processor=fp)
    _rel_paths, manifest_hash = full_orch.build_manifest(in_scope)
    direct_metas, children = full_orch.split_direct_vs_descendant(in_scope, folder_prefix)
    assert not children
    evidence_items = []
    for direct_meta in direct_metas:
        rel_local = full_orch.folder_rel_path(full_orch.posix(direct_meta.rel_path.as_posix()), folder_prefix)
        evidence_items.append((rel_local, file_summary))

    inputs_hash = full_orch.compute_inputs_hash(evidence_items)
    inventory_hash = full_orch.compute_inventory_hash(["a.py"])
    model = resolve_full_summary_model(cfg)
    prompt_fp = aggregate_prompt_fingerprint("repo", "full", 1)

    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        cache.put_ok(
            kind="repo",
            path=str(root),
            mode="full",
            manifest_hash=manifest_hash,
            inputs_hash=inputs_hash,
            inventory_hash=inventory_hash,
            model=model,
            prompt_fp=prompt_fp,
            summary_text="cached summary",
            file_count=1,
            skipped_count=0,
            reduction_layers=0,
            batch_count=0,
            group_counts=None,
            inventory_count=1,
            scope_label=full_orch.SCOPE_LABEL,
            scope_definition_version=full_orch.SCOPE_DEFINITION_VERSION,
            audit_json=serialize_audit_artifact(
                {
                    "pipeline_version": full_orch.FULL_PIPELINE_VERSION,
                    "runtime_fingerprint": "stale-contract",
                }
            ),
        )
    finally:
        cache.close()

    monkeypatch.setattr(
        full_orch,
        "ensure_summaries",
        lambda **_kwargs: ({str(target): file_summary}, []),
    )
    monkeypatch.setattr(
        full_orch,
        "fetch_ai_text_summary",
        lambda **_kwargs: _valid_repo_summary("a.py"),
    )

    result = full_orch.ensure_repo_summary_full(
        root_abs=str(root),
        scan_files=[meta],
        file_processor=fp,
        app_config=cfg,
    )

    assert result == _valid_repo_summary("a.py")

    cache = AggregateCache(cfg["summary_cache_db_path"])
    try:
        row = cache.get(
            kind="repo",
            path=str(root),
            model=model,
            prompt_fp=prompt_fp,
            mode="full",
            manifest_hash=manifest_hash,
            inputs_hash=inputs_hash,
        )
    finally:
        cache.close()

    assert row is not None
    assert row.get("summary_text") == _valid_repo_summary("a.py")
    audit = parse_audit_artifact(row.get("audit_json"))
    assert audit["runtime_fingerprint"] == full_orch._full_runtime_fingerprint(cfg)
    assert dict(audit.get("cache") or {}) == {"hit": False, "revalidated": False, "batch_count": 0}


def test_full_summary_child_threads_do_not_share_parent_cache(monkeypatch, tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.summarizer.full import orchestrator as full_orch

    root = tmp_path / "repo"
    child = root / "child"
    root.mkdir()
    child.mkdir()
    direct = root / "root.py"
    nested = child / "child.py"
    direct.write_text("print('root')\n", encoding="utf-8")
    nested.write_text("print('child')\n", encoding="utf-8")
    metas = [_make_meta(direct, root), _make_meta(nested, root)]

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
    }

    monkeypatch.setattr(
        full_orch,
        "ensure_summaries",
        lambda **kwargs: ({path: f"summary:{Path(path).name}" for path in kwargs["file_paths"]}, []),
    )
    monkeypatch.setattr(
        full_orch,
        "fetch_ai_text_summary",
        lambda **_kwargs: _valid_folder_summary("root.py", "child/"),
    )

    submitted: list[dict] = []

    class DummyFuture:
        def __init__(self, result: str) -> None:
            self._result = result

        def result(self) -> str:
            return self._result

    class DummyExecutor:
        def __init__(self, max_workers: int) -> None:
            self.max_workers = max_workers

        def __enter__(self) -> "DummyExecutor":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def submit(self, fn, /, *args, **kwargs):
            _ = (fn, args)
            submitted.append(dict(kwargs))
            return DummyFuture(_valid_folder_summary("child.py"))

    monkeypatch.setattr(full_orch.concurrent.futures, "ThreadPoolExecutor", DummyExecutor)
    monkeypatch.setattr(full_orch.concurrent.futures, "as_completed", lambda futures: list(futures))

    result = full_orch.ensure_folder_summary_full(
        folder_abs=str(root),
        root_abs=str(root),
        scan_files=metas,
        file_processor=FileProcessor(),
        app_config=cfg,
    )

    assert result == _valid_folder_summary("root.py", "child/")
    assert submitted
    assert submitted[0]["cache"] is None


def test_repo_full_summary_includes_signal_anchor_descendants(monkeypatch, tmp_path):
    from codemerge.filemerger.api import FileProcessor
    from codemerge.summarizer.full import orchestrator as full_orch

    root = tmp_path / "repo"
    merge_service = root / "codemerge" / "filemerger" / "services" / "merge_service.py"
    router = root / "codemerge" / "summarizer" / "router.py"
    readme = root / "README.md"
    merge_service.parent.mkdir(parents=True)
    router.parent.mkdir(parents=True)
    readme.write_text("# Demo\n", encoding="utf-8")
    merge_service.write_text("def merge():\n    pass\n", encoding="utf-8")
    router.write_text("def route():\n    pass\n", encoding="utf-8")
    metas = [_make_meta(readme, root), _make_meta(merge_service, root), _make_meta(router, root)]

    cfg = {
        "summary_cache_db_path": str(tmp_path / "cache.db"),
        "summary_prompt_version": 1,
        "full_summary_model": "test-model",
    }

    prompts: list[tuple[str, str]] = []

    monkeypatch.setattr(
        full_orch,
        "ensure_summaries",
        lambda **kwargs: ({path: f"summary:{Path(path).name}" for path in kwargs["file_paths"]}, []),
    )

    def fake_fetch_ai_text_summary(*, prompt_text, log_context, **_kwargs):
        prompts.append((str(log_context.get("target_kind") or ""), str(log_context.get("phase") or "") + "::" + prompt_text))
        if str(log_context.get("target_kind") or "") == "folder":
            if "filemerger/" in prompt_text and "summarizer/" in prompt_text:
                return _valid_folder_summary("filemerger/", "summarizer/")
            if "services/" in prompt_text:
                return _valid_folder_summary("services/")
            if "router.py" in prompt_text:
                return _valid_folder_summary("router.py")
            return _valid_folder_summary("README.md")
        return _valid_repo_summary("README.md", "codemerge/")

    monkeypatch.setattr(full_orch, "fetch_ai_text_summary", fake_fetch_ai_text_summary)

    result = full_orch.ensure_repo_summary_full(
        root_abs=str(root),
        scan_files=metas,
        file_processor=FileProcessor(),
        app_config=cfg,
    )

    assert "## System overview" in result
    assert "`README.md`" in result
    assert "`codemerge/`" in result
    repo_prompts = [payload for kind, payload in prompts if kind == "repo"]
    assert repo_prompts
    repo_prompt = repo_prompts[-1]
    assert "codemerge/filemerger/services/merge_service.py" in repo_prompt
    assert "codemerge/summarizer/router.py" in repo_prompt
