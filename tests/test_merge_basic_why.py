import io
import os

from codemerge.filemerger import FileProcessor
from codemerge.filemerger import merge_basic


def test_merge_basic_uses_abs_why_for_relative_paths(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    file_path = root / "a.py"
    file_path.write_text("print('a')\n", encoding="utf-8")

    fp = FileProcessor()
    abs_path = os.path.abspath(str(file_path))
    buf = io.StringIO()
    cwd = os.getcwd()
    try:
        os.chdir(root)
        merge_basic.merge_files(
            fp,
            directory=str(root),
            files_to_merge=["a.py"],
            output_file="",
            output_stream=buf,
            include_file_tree=False,
            summaries_by_file=None,
            content_mode="code_only",
            files_with_summary_errors=[],
            markdown_fences=False,
            markdown_toc=False,
            format_summary=lambda items: "",
            content_mode_map={abs_path: "code"},
            why_by_file={abs_path: "abs_reason"},
        )
    finally:
        os.chdir(cwd)
    assert "# why: abs_reason" in buf.getvalue()
