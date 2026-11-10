import io
import os
from pathlib import Path

from codemerge.filemerger import estimate, merge_basic
from codemerge.core import tokens as core_tokens


class HostStub:
    def __init__(self):
        pass

    def generate_file_tree(
        self,
        directory,
        files,
        ascii_mode=None,
        summarized_folders=None,
        pinned_files=None,
    ):
        return "File Tree"

    def transform_content(self, text, enumerate_lines=False, remove_empty=False):
        return text

    def read_file_content_ex(self, file_path):
        try:
            s = Path(file_path).read_text(encoding="utf-8")
            return s, "ok"
        except Exception:
            return None, "error"

    def file_token_count(self, file_path):
        return core_tokens.count_tokens(Path(file_path).read_text(encoding="utf-8"))

    def render_skeleton_block(self, file_path, root=None, enumerate_lines=False, remove_empty=False):
        return "# SKELETON\n# parser_status: ok\n\ndef estimated():\n    ...\n"

    def _commit_pending(self):
        pass


def _fmt(text):
    return str(text or "")


def test_estimator_matches_writer(tmp_path):
    host = HostStub()
    a = tmp_path / "a.py"
    a.write_text("AAA\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("BBBB\n", encoding="utf-8")

    summaries = {
        str(b): "B (func): do b",
    }

    # Writer
    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(a), str(b)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file=summaries,
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={
            os.path.abspath(str(a)): "code",
            os.path.abspath(str(b)): "summary_short",
        },
        fallback_to_code_on_summary_error=True,
    )
    out = buf.getvalue()
    writer_tokens = core_tokens.count_tokens(out)

    # Estimator
    total_est, content_est = estimate.merge_group_token_details(
        str(tmp_path),
        [str(a), str(b)],
        include_file_tree=False,
        custom_start_label="======= START OF FILE: {file_display} =======",
        custom_end_label="======= END OF FILE: {file_display} =======",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        prompt_template=None,
        remove_empty=False,
        enumerate_lines=False,
        markdown_fences=False,
        markdown_toc=False,
        content_mode="code_only",
        summaries_by_file=summaries,
        read_file_content_ex=host.read_file_content_ex,
        count_tokens=core_tokens.count_tokens,
        generate_file_tree_fn=host.generate_file_tree,
        merge_file_tokens_fn=lambda path, enumerate_lines=False, remove_empty=False: host.file_token_count(path),
        format_summary=_fmt,
        content_mode_map={
            os.path.abspath(str(a)): "code",
            os.path.abspath(str(b)): "summary_short",
        },
        files_with_summary_errors=[],
        fallback_to_code_on_summary_error=True,
    )
    assert abs(total_est - writer_tokens) <= 10  # allow minor formatting deltas
    assert 0 < content_est <= total_est


def test_estimator_matches_writer_for_skeleton_modes(tmp_path):
    host = HostStub()
    a = tmp_path / "a.py"
    a.write_text("def actual():\n    return 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("print('b')\n", encoding="utf-8")

    summaries = {str(b): "B summary"}
    mode_map = {
        os.path.abspath(str(a)): "code_plus_skeleton",
        os.path.abspath(str(b)): "summary_short_plus_skeleton",
    }

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(a), str(b)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file=summaries,
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map=mode_map,
        fallback_to_code_on_summary_error=True,
    )
    writer_tokens = core_tokens.count_tokens(buf.getvalue())

    total_est, content_est = estimate.merge_group_token_details(
        str(tmp_path),
        [str(a), str(b)],
        include_file_tree=False,
        custom_start_label="======= START OF FILE: {file_display} =======",
        custom_end_label="======= END OF FILE: {file_display} =======",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        prompt_template=None,
        remove_empty=False,
        enumerate_lines=False,
        markdown_fences=False,
        markdown_toc=False,
        content_mode="code_only",
        summaries_by_file=summaries,
        read_file_content_ex=host.read_file_content_ex,
        count_tokens=core_tokens.count_tokens,
        generate_file_tree_fn=host.generate_file_tree,
        merge_file_tokens_fn=lambda path, enumerate_lines=False, remove_empty=False: host.file_token_count(path),
        render_skeleton_block_fn=lambda path, enumerate_lines=False, remove_empty=False: host.render_skeleton_block(path, enumerate_lines=enumerate_lines, remove_empty=remove_empty),
        format_summary=_fmt,
        content_mode_map=mode_map,
        files_with_summary_errors=[],
        fallback_to_code_on_summary_error=True,
    )

    assert abs(total_est - writer_tokens) <= 10
    assert 0 < content_est <= total_est


def test_estimator_respects_headers_and_toc(tmp_path):
    host = HostStub()
    a = tmp_path / "alpha.py"
    a.write_text("print('alpha')\n", encoding="utf-8")
    b = tmp_path / "beta.py"
    b.write_text("print('beta')\n", encoding="utf-8")

    summaries = {
        str(a): "Alpha (func): alpha",
        str(b): "Beta (func): beta",
    }

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(a), str(b)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=True,
        summaries_by_file=summaries,
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=True,
        markdown_toc=True,
        format_summary=_fmt,
        content_mode_map={
            os.path.abspath(str(a)): "summary_short",
            os.path.abspath(str(b)): "code",
        },
        fallback_to_code_on_summary_error=True,
    )
    rendered = buf.getvalue()
    writer_tokens = core_tokens.count_tokens(rendered)

    total_est, content_est = estimate.merge_group_token_details(
        str(tmp_path),
        [str(a), str(b)],
        include_file_tree=True,
        custom_start_label="======= START OF FILE: {file_display} =======",
        custom_end_label="======= END OF FILE: {file_display} =======",
        file_tree_start_label="======= FILE TREE =======",
        file_tree_end_label="======= END OF FILE TREE =======",
        prompt_template=None,
        remove_empty=False,
        enumerate_lines=False,
        markdown_fences=True,
        markdown_toc=True,
        content_mode="code_only",
        summaries_by_file=summaries,
        read_file_content_ex=host.read_file_content_ex,
        count_tokens=core_tokens.count_tokens,
        generate_file_tree_fn=host.generate_file_tree,
        merge_file_tokens_fn=lambda path, enumerate_lines=False, remove_empty=False: host.file_token_count(path),
        format_summary=_fmt,
        content_mode_map={
            os.path.abspath(str(a)): "summary_short",
            os.path.abspath(str(b)): "code",
        },
        files_with_summary_errors=[],
        fallback_to_code_on_summary_error=True,
    )

    assert abs(total_est - writer_tokens) <= 20
    assert 0 < content_est <= total_est


def test_estimator_handles_empty_fenced_file(tmp_path):
    host = HostStub()
    empty = tmp_path / "empty.py"
    empty.write_text("", encoding="utf-8")

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(empty)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={},
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=True,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={
            os.path.abspath(str(empty)): "code",
        },
        fallback_to_code_on_summary_error=True,
    )
    rendered = buf.getvalue()
    writer_tokens = core_tokens.count_tokens(rendered)

    total_est, content_est = estimate.merge_group_token_details(
        str(tmp_path),
        [str(empty)],
        include_file_tree=False,
        custom_start_label="======= START OF FILE: {file_display} =======",
        custom_end_label="======= END OF FILE: {file_display} =======",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        prompt_template=None,
        remove_empty=False,
        enumerate_lines=False,
        markdown_fences=True,
        markdown_toc=False,
        content_mode="code_only",
        summaries_by_file={},
        read_file_content_ex=host.read_file_content_ex,
        count_tokens=core_tokens.count_tokens,
        generate_file_tree_fn=host.generate_file_tree,
        merge_file_tokens_fn=lambda path, enumerate_lines=False, remove_empty=False: host.file_token_count(path),
        format_summary=_fmt,
        content_mode_map={
            os.path.abspath(str(empty)): "code",
        },
        files_with_summary_errors=[],
        fallback_to_code_on_summary_error=True,
    )

    assert abs(total_est - writer_tokens) <= 5
    assert 0 <= content_est <= total_est


def test_estimator_accounts_for_why_by_file(tmp_path):
    host = HostStub()
    a = tmp_path / "a.py"
    a.write_text("print('a')\n", encoding="utf-8")

    why = {str(a): "selected_by_prune"}

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(a)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={},
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(a)): "code"},
        fallback_to_code_on_summary_error=True,
        why_by_file=why,
    )
    writer_tokens = core_tokens.count_tokens(buf.getvalue())

    total_est, content_est = estimate.merge_group_token_details(
        str(tmp_path),
        [str(a)],
        include_file_tree=False,
        custom_start_label="======= START OF FILE: {file_display} =======",
        custom_end_label="======= END OF FILE: {file_display} =======",
        file_tree_start_label="TREE START",
        file_tree_end_label="TREE END",
        prompt_template=None,
        remove_empty=False,
        enumerate_lines=False,
        markdown_fences=False,
        markdown_toc=False,
        content_mode="code_only",
        summaries_by_file={},
        read_file_content_ex=host.read_file_content_ex,
        count_tokens=core_tokens.count_tokens,
        generate_file_tree_fn=host.generate_file_tree,
        merge_file_tokens_fn=lambda path, enumerate_lines=False, remove_empty=False: host.file_token_count(path),
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(a)): "code"},
        files_with_summary_errors=[],
        fallback_to_code_on_summary_error=True,
        why_by_file=why,
    )

    assert abs(total_est - writer_tokens) <= 5
    assert 0 <= content_est <= total_est
