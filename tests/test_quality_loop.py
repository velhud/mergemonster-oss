from __future__ import annotations


def test_sanitize_template_placeholder_literals_rewrites_prompt_tokens():
    from codemerge.summarizer.shared.quality import sanitize_template_placeholder_literals

    rendered = sanitize_template_placeholder_literals(
        "Templates expose `{{PROFILE}}`, {{REPO_NAME}}, and {{OUTPUT OUTLINE}}."
    )

    assert "{{PROFILE}}" not in rendered
    assert "{{REPO_NAME}}" not in rendered
    assert "{{OUTPUT OUTLINE}}" not in rendered
    assert "`PROFILE` placeholder" in rendered
    assert "REPO_NAME placeholder" in rendered
    assert "OUTPUT OUTLINE placeholder" in rendered


def test_find_missing_required_headings_reports_exact_missing_items():
    from codemerge.summarizer.shared.quality import find_missing_required_headings

    missing = find_missing_required_headings(
        "# Folder summary\n## Role in the system\n- ok\n",
        required_headings=("## Role in the system", "## Where to change X", "## Coverage note"),
    )

    assert missing == ["## Where to change X", "## Coverage note"]


def test_validate_full_summary_contract_reports_uncovered_inventory_and_placeholder_leaks():
    from codemerge.summarizer.shared.quality import validate_full_summary_contract

    validation = validate_full_summary_contract(
        "# Folder summary\n## Role in the system\n- `a.py`\n## Where to change X\n- To change a, look at `a.py`.\n## Coverage note\n- ok\n{{LEFTOVER}}",
        required_headings=("## Role in the system", "## Where to change X", "## Coverage note"),
        inventory_items=("a.py", "b.py"),
        scope_path_prefix="src/core",
    )

    assert validation["missing_headings"] == []
    assert validation["placeholder_leaks"] == ["LEFTOVER"]
    assert validation["uncovered_inventory_items"] == ["b.py"]
    assert validation["inventory_covered"] is False


def test_augment_missing_inventory_change_pointers_injects_exact_items():
    from codemerge.summarizer.shared.change_pointers import augment_missing_inventory_change_pointers
    from codemerge.summarizer.shared.quality import validate_full_summary_contract

    rendered = augment_missing_inventory_change_pointers(
        "# Folder summary\n"
        "## Role in the system\n"
        "- `a.py`\n"
        "## Where to change X\n"
        "- To change a, look at `a.py`.\n"
        "## Coverage note\n"
        "- ok\n",
        missing_items=("protocol.md", "examples.md"),
        headings=("## Where to change X",),
    )

    assert "`protocol.md`" in rendered
    assert "`examples.md`" in rendered
    validation = validate_full_summary_contract(
        rendered,
        required_headings=("## Role in the system", "## Where to change X", "## Coverage note"),
        inventory_items=("a.py", "protocol.md", "examples.md"),
        scope_path_prefix="codemerge/summarizer/prompts",
    )
    assert validation["uncovered_inventory_items"] == []
    assert validation["inventory_covered"] is True


def test_augment_missing_inventory_change_pointers_requires_literal_folder_tokens():
    from codemerge.summarizer.shared.change_pointers import augment_missing_inventory_change_pointers

    rendered = augment_missing_inventory_change_pointers(
        "# Folder summary\n"
        "## Role in the system\n"
        "- Handles full and quick summary modes.\n"
        "## Where to change X\n"
        "- To change summary mode dispatch, start at `router.py`.\n"
        "## Coverage note\n"
        "- ok\n",
        missing_items=("full/", "quick/"),
        headings=("## Where to change X",),
    )

    assert "`full/`" in rendered
    assert "`quick/`" in rendered


def test_augment_missing_inventory_change_pointers_does_not_treat_longer_child_paths_as_folder_hits():
    from codemerge.summarizer.shared.change_pointers import augment_missing_inventory_change_pointers

    rendered = augment_missing_inventory_change_pointers(
        "# Folder summary\n"
        "## Role in the system\n"
        "- Uses `codemerge/summarizer/full/orchestrator.py` and `codemerge/summarizer/quick/orchestrator.py`.\n"
        "## Where to change X\n"
        "- To change summary orchestration, start at `codemerge/summarizer/full/orchestrator.py`.\n"
        "## Coverage note\n"
        "- ok\n",
        missing_items=("full/", "quick/"),
        headings=("## Where to change X",),
    )

    assert rendered.count("`full/`") >= 1
    assert rendered.count("`quick/`") >= 1


def test_augment_missing_heading_sections_appends_required_heading():
    from codemerge.summarizer.shared.change_pointers import augment_missing_heading_sections
    from codemerge.summarizer.shared.quality import validate_full_summary_contract

    rendered = augment_missing_heading_sections(
        "# Repository: `demo`\n"
        "## System overview\n"
        "- ok\n"
        "## Major subsystems\n"
        "- ok\n"
        "## Entry points / how it runs\n"
        "- ok\n",
        required_headings=(
            "## System overview",
            "## Major subsystems",
            "## Entry points / how it runs",
            "## Cross-subsystem flows",
        ),
    )

    assert "## Cross-subsystem flows" in rendered
    validation = validate_full_summary_contract(
        rendered,
        required_headings=(
            "## System overview",
            "## Major subsystems",
            "## Entry points / how it runs",
            "## Cross-subsystem flows",
        ),
        inventory_items=(),
        scope_path_prefix="",
    )
    assert validation["missing_headings"] == []


def test_invalid_path_detector_ignores_urls_and_slash_phrases():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual._find_invalid_path_references(
        (
            "Use request/response handlers, show/hide toggles, and retry/back logic. "
            "External systems include Groq/OpenAI and https://api.groq.com/openai/v1/responses."
        ),
        inventory_items=("__init__.py", "__main__.py", "server.py", "static/"),
        manifest_paths=(
            "codemerge/jsui/__init__.py",
            "codemerge/jsui/__main__.py",
            "codemerge/jsui/server.py",
            "codemerge/jsui/static/app.js",
        ),
        scope_path_prefix="codemerge/jsui",
    )

    assert invalid == []


def test_invalid_path_detector_ignores_api_route_fragments_inside_backticks():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual.find_invalid_path_references(
        "Front-end requests `/api/scan_job`, `/api/jobs/{job_id}`, and `/api/merge_*`.",
        inventory_items=("__main__.py", "server.py", "static/"),
        manifest_paths=(
            "codemerge/jsui/__main__.py",
            "codemerge/jsui/server.py",
            "codemerge/jsui/static/app.js",
        ),
        scope_path_prefix="codemerge/jsui",
    )

    assert invalid == []


def test_invalid_path_detector_keeps_known_root_out_of_scope_paths():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual._find_invalid_path_references(
        "Entry points mention `src/provider.py` and valid local asset `static/app.js`.",
        inventory_items=("__init__.py", "__main__.py", "server.py", "static/"),
        manifest_paths=(
            "codemerge/jsui/__init__.py",
            "codemerge/jsui/__main__.py",
            "codemerge/jsui/server.py",
            "codemerge/jsui/static/app.js",
        ),
        scope_path_prefix="codemerge/jsui",
    )

    assert invalid == ["src/provider.py"]


def test_invalid_path_detector_accepts_unique_extensionless_file_stems():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual._find_invalid_path_references(
        "Change `services/merge_service` and `services/diagnostics_service` for merge flow work.",
        inventory_items=("api.py", "config.py", "services/"),
        manifest_paths=(
            "codemerge/filemerger/api.py",
            "codemerge/filemerger/config.py",
            "codemerge/filemerger/services/merge_service.py",
            "codemerge/filemerger/services/diagnostics_service.py",
        ),
        scope_path_prefix="codemerge/filemerger",
    )

    assert invalid == []


def test_invalid_path_detector_accepts_valid_folder_wildcards():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual._find_invalid_path_references(
        "Prompt templates live under `codemerge/summarizer/prompts/*.md` and shared helpers under `shared/*`.",
        inventory_items=("prompts/", "shared/", "__init__.py"),
        manifest_paths=(
            "codemerge/summarizer/__init__.py",
            "codemerge/summarizer/prompts/repo_quick_summary.md",
            "codemerge/summarizer/prompts/folder_full_summary.md",
            "codemerge/summarizer/shared/audit.py",
        ),
        scope_path_prefix="codemerge/summarizer",
    )

    assert invalid == []


def test_invalid_path_detector_accepts_nested_folder_aliases():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual._find_invalid_path_references(
        "Prompt templates live under `codemerge/summarizer/prompts/`.",
        inventory_items=("prompts/", "shared/", "__init__.py"),
        manifest_paths=(
            "codemerge/summarizer/__init__.py",
            "codemerge/summarizer/prompts/repo_quick_summary.md",
            "codemerge/summarizer/prompts/folder_full_summary.md",
            "codemerge/summarizer/shared/audit.py",
        ),
        scope_path_prefix="codemerge/summarizer",
    )

    assert invalid == []


def test_invalid_path_detector_accepts_in_scope_subtree_roots_but_not_excluded_roots():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual.find_invalid_path_references(
        (
            "Major subsystems include `codemerge/core/`, `codemerge/filemerger/`, "
            "and the excluded `codemerge/jsui/` area."
        ),
        inventory_items=("README.md", "codemerge/", "main.py"),
        manifest_paths=(
            "README.md",
            "main.py",
            "codemerge/core/__init__.py",
            "codemerge/filemerger/api.py",
            "codemerge/summarizer/router.py",
        ),
        scope_path_prefix="",
    )

    assert invalid == ["codemerge/jsui/"]


def test_invalid_path_detector_ignores_platform_slash_phrases():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual.find_invalid_path_references(
        "Clipboard helpers support macOS/Windows/Linux without changing repo layout.",
        inventory_items=("README.md", "codemerge/", "main.py"),
        manifest_paths=(
            "README.md",
            "main.py",
            "codemerge/filemerger/api.py",
        ),
        scope_path_prefix="",
    )

    assert invalid == []


def test_invalid_path_detector_ignores_known_state_partition_phrase():
    from codemerge.summarizer.shared import quality as qual

    invalid = qual.find_invalid_path_references(
        "The cache partitions files into fresh/stale/missing before new LLM work.",
        inventory_items=("change_detect.py", "orchestrator.py"),
        manifest_paths=(
            "codemerge/summarizer/shared/change_detect.py",
            "codemerge/summarizer/shared/orchestrator.py",
        ),
        scope_path_prefix="codemerge/summarizer/shared",
    )

    assert invalid == []


def test_invalid_path_detector_allows_existing_repo_dependencies_outside_scope(tmp_path):
    from codemerge.summarizer.shared import quality as qual

    repo = tmp_path / "repo"
    (repo / "codemerge" / "summarizer" / "shared").mkdir(parents=True)
    (repo / "codemerge" / "core").mkdir(parents=True)
    (repo / "codemerge" / "summarizer" / "shared" / "quality.py").write_text("pass\n", encoding="utf-8")
    (repo / "codemerge" / "core" / "normalize.py").write_text("pass\n", encoding="utf-8")
    (repo / "codemerge" / "models.py").write_text("pass\n", encoding="utf-8")

    invalid = qual.find_invalid_path_references(
        "Shared helpers depend on `codemerge/core/normalize.py` and `codemerge/models`.",
        inventory_items=("quality.py",),
        manifest_paths=("codemerge/summarizer/shared/quality.py",),
        scope_path_prefix="codemerge/summarizer/shared",
        repo_root_abs=str(repo),
        allow_existing_repo_paths_outside_scope=True,
    )

    assert invalid == []


def test_sanitize_scoped_summary_paths_rewrites_invalid_tokens():
    from codemerge.summarizer.shared import quality as qual

    sanitized, invalid = qual.sanitize_scoped_summary_paths(
        "Investigate `codemerge/jsui/server.py` but keep `codemerge/filemerger/api.py`.",
        inventory_items=("main.py", "codemerge/"),
        manifest_paths=(
            "main.py",
            "codemerge/filemerger/api.py",
        ),
        scope_path_prefix="",
    )

    assert invalid == ["codemerge/jsui/server.py"]
    assert "codemerge/jsui/server.py" not in sanitized
    assert "the jsui server module" in sanitized
    assert "codemerge/filemerger/api.py" in sanitized


def test_sanitize_scoped_summary_paths_preserves_existing_repo_dependencies(tmp_path):
    from codemerge.summarizer.shared import quality as qual

    repo = tmp_path / "repo"
    (repo / "codemerge" / "summarizer" / "shared").mkdir(parents=True)
    (repo / "codemerge" / "core").mkdir(parents=True)
    (repo / "codemerge" / "summarizer" / "shared" / "quality.py").write_text("pass\n", encoding="utf-8")
    (repo / "codemerge" / "core" / "normalize.py").write_text("pass\n", encoding="utf-8")

    sanitized, invalid = qual.sanitize_scoped_summary_paths(
        "Dependency edge: `codemerge/core/normalize.py`.",
        inventory_items=("quality.py",),
        manifest_paths=("codemerge/summarizer/shared/quality.py",),
        scope_path_prefix="codemerge/summarizer/shared",
        repo_root_abs=str(repo),
        allow_existing_repo_paths_outside_scope=True,
        replacement_style="redact",
    )

    assert invalid == []
    assert sanitized == "Dependency edge: `codemerge/core/normalize.py`."


def test_sanitize_scoped_summary_paths_redacts_absolute_leading_slash_paths():
    from codemerge.summarizer.shared import quality as qual

    sanitized, invalid = qual.sanitize_scoped_summary_paths(
        'Cache path is "/Users/example/.codemerge_summary_cache.db".',
        inventory_items=("README.md",),
        manifest_paths=("README.md",),
        scope_path_prefix="",
        replacement_style="redact",
    )

    assert invalid == ["Users/example/.codemerge_summary_cache.db"]
    assert "/Users/example/.codemerge_summary_cache.db" not in sanitized
    assert "[unverified path omitted]" in sanitized
