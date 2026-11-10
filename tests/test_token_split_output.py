import json
from pathlib import Path

from codemerge.filemerger import FileProcessor


def test_token_split_writes_toc_and_fences(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    a = root / "a.py"
    a.write_text("print('a')\n", encoding="utf-8")
    b = root / "b.txt"
    b.write_text("hello\n", encoding="utf-8")

    out_folder = tmp_path / "out"
    fp = FileProcessor()
    fp.merge_files_token_split(
        directory=str(root),
        files_to_merge=[str(a), str(b)],
        output_folder=str(out_folder),
        token_limit=100000,
        progress_callback=None,
        custom_start_label="START {file_display}",
        custom_end_label="END {file_display}",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        include_file_tree=False,
        create_json_manifest=False,
        markdown_fences=True,
        markdown_toc=True,
        summaries_by_file=None,
        content_mode="code_only",
        files_with_summary_errors=None,
        prompt_template=None,
        verb="Merging",
        remove_empty=False,
        enumerate_lines=False,
    )

    chunk = out_folder / "1.txt"
    text = chunk.read_text(encoding="utf-8")
    assert "## Table of Contents" in text
    assert "```py" in text
    assert "```txt" in text


def test_token_split_manifest_uses_processed_tokens(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.py"
    f.write_text("line1\n\nline3\n", encoding="utf-8")

    out_folder = tmp_path / "out_manifest"
    fp = FileProcessor()
    fp.merge_files_token_split(
        directory=str(root),
        files_to_merge=[str(f)],
        output_folder=str(out_folder),
        token_limit=100000,
        progress_callback=None,
        custom_start_label="START {file_display}",
        custom_end_label="END {file_display}",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        include_file_tree=False,
        create_json_manifest=True,
        markdown_fences=False,
        markdown_toc=False,
        summaries_by_file=None,
        content_mode="code_only",
        files_with_summary_errors=None,
        prompt_template=None,
        verb="Merging",
        remove_empty=True,
        enumerate_lines=True,
    )

    manifest = Path(out_folder) / "multi-merge.manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    merged = data["groups"][0]["merged_files"][0]
    expected = fp.processed_token_count(
        str(f),
        enumerate_lines=True,
        remove_empty=True,
    )
    assert merged["token_count"] == expected


def test_token_split_includes_virtual_blocks(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.py"
    f.write_text("print('a')\n", encoding="utf-8")

    out_folder = tmp_path / "out_virtual"
    fp = FileProcessor()
    fp.merge_files_token_split(
        directory=str(root),
        files_to_merge=[str(f)],
        output_folder=str(out_folder),
        token_limit=100000,
        progress_callback=None,
        custom_start_label="START {file_display}",
        custom_end_label="END {file_display}",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        include_file_tree=False,
        create_json_manifest=False,
        markdown_fences=False,
        markdown_toc=False,
        summaries_by_file=None,
        content_mode="code_only",
        files_with_summary_errors=None,
        prompt_template=None,
        verb="Merging",
        remove_empty=False,
        enumerate_lines=False,
        virtual_blocks=[
            {
                "kind": "repo",
                "display": str(root),
                "text": "Repo summary text",
                "variant": "full",
            }
        ],
    )

    chunk = out_folder / "1.txt"
    text = chunk.read_text(encoding="utf-8")
    assert "REPOSITORY SUMMARY (FULL)" in text
    assert "Repo summary text" in text


def test_token_split_manifest_counts_summary_plus_skeleton_rendered_content(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.py"
    f.write_text("def hidden():\n    return 'body'\n", encoding="utf-8")

    out_folder = tmp_path / "out_skeleton_manifest"
    fp = FileProcessor()
    content_mode_map = {str(f.resolve()): "summary_short_plus_skeleton"}
    summaries = {str(f): "Hidden summary"}
    fp.merge_files_token_split(
        directory=str(root),
        files_to_merge=[str(f)],
        output_folder=str(out_folder),
        token_limit=100000,
        progress_callback=None,
        custom_start_label="START {file_display}",
        custom_end_label="END {file_display}",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        include_file_tree=False,
        create_json_manifest=True,
        markdown_fences=False,
        markdown_toc=False,
        summaries_by_file=summaries,
        content_mode="code_only",
        files_with_summary_errors=None,
        prompt_template=None,
        verb="Merging",
        remove_empty=False,
        enumerate_lines=False,
        content_mode_map=content_mode_map,
    )

    chunk = out_folder / "1.txt"
    text = chunk.read_text(encoding="utf-8")
    assert "# Hidden summary" in text
    assert "# SKELETON" in text
    assert "def hidden():" in text
    assert "return 'body'" not in text

    manifest = Path(out_folder) / "multi-merge.manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    merged = data["groups"][0]["merged_files"][0]
    _total, content_only = fp.merge_group_token_details(
        str(root),
        [str(f)],
        include_file_tree=False,
        custom_start_label="START {file_display}",
        custom_end_label="END {file_display}",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        prompt_template=None,
        remove_empty=False,
        enumerate_lines=False,
        markdown_fences=False,
        markdown_toc=False,
        content_mode="code_only",
        summaries_by_file=summaries,
        content_mode_map=content_mode_map,
        files_with_summary_errors=[],
    )
    assert merged["token_count"] == content_only


def test_split_lines_renders_skeleton_without_source_body(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    f = root / "a.py"
    f.write_text("def hidden():\n    return 'body'\n", encoding="utf-8")

    out_folder = tmp_path / "out_split_skeleton"
    fp = FileProcessor()
    fp.merge_files_into_multiple(
        directory=str(root),
        files_to_merge=[str(f)],
        output_folder=str(out_folder),
        num_text_files=1,
        progress_callback=None,
        custom_start_label="START {file_display}",
        custom_end_label="END {file_display}",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        include_file_tree=False,
        create_json_manifest=True,
        summaries_by_file=None,
        content_mode="code_only",
        files_with_summary_errors=None,
        prompt_template=None,
        verb="Merging",
        remove_empty=False,
        enumerate_lines=False,
        content_mode_map={str(f.resolve()): "skeleton"},
    )

    text = (out_folder / "1.txt").read_text(encoding="utf-8")
    assert "# SKELETON" in text
    assert "def hidden():" in text
    assert "return 'body'" not in text
