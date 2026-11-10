from __future__ import annotations

from codemerge.llm import _summarize_prompts as prompt_mod
from codemerge.llm._summarize_prompts import (
    aggregate_prompt_fingerprint,
    build_folder_summary_prompt,
    build_repo_summary_prompt,
)


def test_folder_full_prompt_uses_exact_where_to_change_heading() -> None:
    prompt = build_folder_summary_prompt(
        "full",
        profile="merge",
        repo_name="demo",
        folder_rel="src/core",
        manifest_id="manifest-1",
        scope_label="text_only_excluding_binary_assets",
        scope_def_version=1,
        inventory_list="- a.py\n- b.py",
        tree_snapshot="src/core",
        anchor_blocks="(none)",
        evidence_blocks="(none)",
    )

    assert "# Folder summary" in prompt
    assert "## Where to change X\n" in prompt
    assert "## Where to change X (practical pointers)" not in prompt
    assert "Every INVENTORY item must appear at least once" in prompt
    assert "not path-like tokens" in prompt
    assert "Do not enumerate dotted module names, class names, member calls, or function names in this section" in prompt
    assert "runtime or CLI entrypoint" in prompt
    assert "configuration, manifest, cache, or orchestration file" in prompt
    assert "browser UI, HTTP routes, background jobs, or provider wiring" in prompt
    assert "codemerge/" not in prompt


def test_repo_full_prompt_uses_exact_where_to_change_heading() -> None:
    prompt = build_repo_summary_prompt(
        "full",
        profile="merge",
        repo_name="demo",
        manifest_id="manifest-1",
        scope_label="text_only_excluding_binary_assets",
        scope_def_version=1,
        inventory_list="- api/\n- main.py",
        tree_snapshot="api\nmain.py",
        anchor_blocks="(none)",
        evidence_blocks="(none)",
    )

    assert "## Where to change X\n" in prompt
    assert "## Where to change X (practical pointers)" not in prompt
    assert "Every INVENTORY item must appear at least once" in prompt
    assert "not path-like tokens" in prompt
    assert "When root files are in scope, mention them by exact root filename" in prompt
    assert "include explicit `sqlite` and `filesystem` bullets" in prompt
    assert "keep the `.py` suffix" in prompt
    assert "rewrite them in plain English instead of preserving the literal token" in prompt
    assert "api.merge()" in prompt
    assert "selector.select()" in prompt
    assert "service-layer change point or config file" in prompt
    assert "startup file, or orchestrator" in prompt
    assert "codemerge/filemerger/services/merge_service.py" not in prompt
    assert "OpenAI/Groq/Llama" not in prompt


def test_folder_quick_prompt_mentions_manifest_child_path() -> None:
    prompt = build_folder_summary_prompt(
        "quick",
        profile="merge",
        repo_name="demo",
        folder_rel="src/core",
        manifest_id="manifest-1",
        scope_label="text_only_excluding_binary_assets",
        scope_def_version=1,
        inventory_list="- manifest.py\n- a.py",
        tree_snapshot="src/core",
        anchor_blocks="(none)",
        evidence_blocks="(none)",
    )

    assert "What to look at first" in prompt
    assert "manifest, cache, or scope bookkeeping" in prompt
    assert "codemerge/summarizer/prompts/" not in prompt


def test_full_aggregate_prompt_fingerprint_tracks_full_prompt_template(monkeypatch) -> None:
    base = aggregate_prompt_fingerprint("folder", "full", 7)

    monkeypatch.setattr(
        prompt_mod,
        "FOLDER_FULL_TEMPLATE",
        prompt_mod.FOLDER_FULL_TEMPLATE + "\nEXTRA RULE",
    )

    changed = aggregate_prompt_fingerprint("folder", "full", 7)

    assert changed != base


def test_quick_aggregate_prompt_fingerprint_tracks_selector_prompt(monkeypatch) -> None:
    base = aggregate_prompt_fingerprint("folder", "quick", 7)

    monkeypatch.setattr(
        prompt_mod,
        "QUICK_EVIDENCE_FOLDER_TEMPLATE",
        prompt_mod.QUICK_EVIDENCE_FOLDER_TEMPLATE + "\nEXTRA SELECTOR RULE",
    )

    changed = aggregate_prompt_fingerprint("folder", "quick", 7)

    assert changed != base
