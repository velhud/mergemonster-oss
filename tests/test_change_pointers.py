from codemerge.summarizer.shared.change_pointers import augment_change_pointer_section


def test_augment_change_pointer_section_adds_grounded_repo_bullets():
    summary = (
        "## Where to change X\n"
        "- Existing bullet.\n"
        "\n"
        "## Uncertainties\n"
        "- None.\n"
    )
    result = augment_change_pointer_section(
        summary,
        candidate_paths=(
            "codemerge/filemerger/services/merge_service.py",
            "codemerge/jsui/server.py",
            "codemerge/summarizer/router.py",
        ),
        headings=("## Where to change X",),
    )

    assert "merge preview / merge flow / merge integration" in result
    assert "codemerge/filemerger/services/merge_service.py" in result
    assert "repo summary injection / repo summary scope" in result
    assert "codemerge/jsui/server.py" in result
    assert "summary router or summary orchestration" in result
    assert "codemerge/summarizer/router.py" in result


def test_augment_change_pointer_section_does_not_duplicate_existing_topic_and_path():
    summary = (
        "## Where to change behavior first\n"
        "- To change config defaults, start at `codemerge/filemerger/config.py` because it owns config.\n"
    )
    result = augment_change_pointer_section(
        summary,
        candidate_paths=("codemerge/filemerger/config.py",),
        headings=("## Where to change behavior first",),
    )

    assert result.count("codemerge/filemerger/config.py") == 1


def test_augment_change_pointer_section_ignores_non_actionable_mentions_outside_change_section():
    summary = (
        "## Major subsystems\n"
        "- `codemerge/summarizer/router.py` (summary orchestration).\n"
    )
    result = augment_change_pointer_section(
        summary,
        candidate_paths=("codemerge/summarizer/router.py",),
        headings=("## Where to change behavior first",),
    )

    assert "## Where to change behavior first" in result
    assert "To change summary router or summary orchestration" in result
    assert result.count("codemerge/summarizer/router.py") == 2
