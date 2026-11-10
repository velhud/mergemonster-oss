import io
import os
from pathlib import Path

import pytest

from codemerge.core.controller import CoreController
from codemerge.filemerger import merge_basic
from codemerge.models import FileMode


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
        return len(Path(file_path).read_text(encoding="utf-8"))

    def render_skeleton_block(self, file_path, root=None, enumerate_lines=False, remove_empty=False):
        return "# SKELETON\n# parser_status: ok\n\ndef stubbed():\n    ...\n"

    def _commit_pending(self):
        pass


def _fmt(summary_text: str) -> str:
    return summary_text


def test_writer_honors_content_mode_map(tmp_path):
    host = HostStub()
    a = tmp_path / "a.py"
    a.write_text("print('A')\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("print('B')\n", encoding="utf-8")
    c = tmp_path / "c.py"
    c.write_text("print('C')\n", encoding="utf-8")

    summaries = {
        str(b): "B (func): do b",
    }

    buf = io.StringIO()
    ok = merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(a), str(b), str(c)],
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
            os.path.abspath(str(c)): "exclude",
        },
        fallback_to_code_on_summary_error=True,
    )
    assert ok
    out = buf.getvalue()
    assert "print('A')" in out
    assert "B (func): do b" in out
    assert "print('B')" not in out  # summary-only for b
    assert "print('C')" not in out  # excluded


def test_writer_emits_code_and_summary_for_code_plus(tmp_path):
    host = HostStub()
    path = tmp_path / "combo.py"
    path.write_text("print('combo')\n", encoding="utf-8")

    summaries = {
        str(path): "Combo (func): mixed",
    }

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(path)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file=summaries,
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(path)): "code_plus_short"},
        fallback_to_code_on_summary_error=True,
    )
    out = buf.getvalue()
    assert "Combo (func): mixed" in out
    assert "print('combo')" in out


def test_writer_emits_skeleton_only_without_code(tmp_path):
    host = HostStub()
    path = tmp_path / "skel.py"
    path.write_text("def actual():\n    return 42\n", encoding="utf-8")

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(path)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={},
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(path)): "skeleton"},
        fallback_to_code_on_summary_error=True,
    )
    out = buf.getvalue()
    assert "# SKELETON" in out
    assert "def stubbed():" in out
    assert "return 42" not in out


def test_writer_emits_code_plus_skeleton(tmp_path):
    host = HostStub()
    path = tmp_path / "combo_skel.py"
    path.write_text("print('combo skel')\n", encoding="utf-8")

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(path)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={},
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(path)): "code_plus_skeleton"},
        fallback_to_code_on_summary_error=True,
    )
    out = buf.getvalue()
    assert "# SKELETON" in out
    assert "print('combo skel')" in out


def test_writer_emits_summary_plus_skeleton_without_code(tmp_path):
    host = HostStub()
    path = tmp_path / "summary_skel.py"
    path.write_text("print('hidden')\n", encoding="utf-8")

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(path)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={str(path): "Summary line"},
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(path)): "summary_short_plus_skeleton"},
        fallback_to_code_on_summary_error=True,
    )
    out = buf.getvalue()
    assert "# Summary line" in out
    assert "# SKELETON" in out
    assert "print('hidden')" not in out


def test_writer_summary_plus_skeleton_error_does_not_fallback_to_code(tmp_path):
    host = HostStub()
    path = tmp_path / "summary_skel_error.py"
    path.write_text("print('must not leak')\n", encoding="utf-8")

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(path)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={str(path): None},
        content_mode="code_only",
        files_with_summary_errors=[str(path)],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(path)): "summary_short_plus_skeleton"},
        fallback_to_code_on_summary_error=True,
    )
    out = buf.getvalue()
    assert "# Summary was unavailable." in out
    assert "# SKELETON" in out
    assert "print('must not leak')" not in out
    assert "# SUMMARY FAILED" not in out


def test_writer_summary_error_fallback_comment(tmp_path):
    host = HostStub()
    path = tmp_path / "err.py"
    path.write_text("print('err')\n", encoding="utf-8")

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(path)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={str(path): None},
        content_mode="code_only",
        files_with_summary_errors=[str(path)],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(path)): "summary_short"},
        fallback_to_code_on_summary_error=True,
    )
    out = buf.getvalue()
    assert "# SUMMARY FAILED – using code instead" in out
    assert "print('err')" in out


def test_writer_summary_error_no_fallback_keeps_placeholder(tmp_path):
    host = HostStub()
    path = tmp_path / "nofallback.py"
    path.write_text("print('nope')\n", encoding="utf-8")

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=[str(path)],
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file={str(path): None},
        content_mode="code_only",
        files_with_summary_errors=[str(path)],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map={os.path.abspath(str(path)): "summary_short"},
        fallback_to_code_on_summary_error=False,
    )
    out = buf.getvalue()
    assert "Summary was unavailable" in out
    assert "print('nope')" not in out


def test_merge_folder_summary_suppresses_descendants(tmp_path):
    host = HostStub()
    folder = tmp_path / "a"
    folder.mkdir()
    x = folder / "x.py"
    x.write_text("print('x')\n", encoding="utf-8")
    y = folder / "y.py"
    y.write_text("print('y')\n", encoding="utf-8")
    root_file = tmp_path / "root.py"
    root_file.write_text("print('root')\n", encoding="utf-8")

    controller = CoreController()
    controller.per_folder_modes = {str(folder.resolve()): "FOLDER_SUMMARY"}
    controller.per_file_modes = {str(x.resolve()): FileMode.CODE}

    files = [str(x), str(y), str(root_file)]
    plan = controller.build_plan(files)
    assert plan[os.path.abspath(str(y))] == FileMode.EXCLUDE

    files_for_merge = [
        p for p in files if plan.get(os.path.abspath(p)) != FileMode.EXCLUDE
    ]
    content_mode_map = {
        path: controller._mode_to_content_str(mode) for path, mode in plan.items()
    }

    buf = io.StringIO()
    merge_basic.merge_files(
        host,
        directory=str(tmp_path),
        files_to_merge=files_for_merge,
        output_file=str(tmp_path / "out.md"),
        output_stream=buf,
        include_file_tree=False,
        summaries_by_file=None,
        summaries_short=None,
        summaries_long=None,
        strict_summary_required=False,
        content_mode="code_only",
        files_with_summary_errors=[],
        markdown_fences=False,
        markdown_toc=False,
        format_summary=_fmt,
        content_mode_map=content_mode_map,
        fallback_to_code_on_summary_error=True,
        virtual_blocks=[
            {"kind": "folder", "display": str(folder.resolve()), "text": "Folder summary"}
        ],
    )
    out = buf.getvalue()
    assert "START OF FOLDER SUMMARY" in out
    assert "print('x')" in out
    assert "print('y')" not in out


def test_merge_strict_summary_required(tmp_path):
    host = HostStub()
    path = tmp_path / "needs_summary.py"
    path.write_text("print('need')\n", encoding="utf-8")

    buf = io.StringIO()
    with pytest.raises(RuntimeError, match="Summary required but unavailable"):
        merge_basic.merge_files(
            host,
            directory=str(tmp_path),
            files_to_merge=[str(path)],
            output_file=str(tmp_path / "out.md"),
            output_stream=buf,
            include_file_tree=False,
            summaries_by_file=None,
            summaries_short=None,
            summaries_long=None,
            strict_summary_required=True,
            content_mode="code_only",
            files_with_summary_errors=[],
            markdown_fences=False,
            markdown_toc=False,
            format_summary=_fmt,
            content_mode_map={os.path.abspath(str(path)): "summary_short"},
            fallback_to_code_on_summary_error=True,
        )
