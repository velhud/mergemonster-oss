from codemerge.filemerger import FileProcessor


def test_scan_directory_basic(tmp_path):
    (tmp_path / "foo.py").write_text("import sys")
    (tmp_path / "bar.txt").write_text("hi")
    fp = FileProcessor()
    ctx = fp.scan_directory(
        str(tmp_path),
        file_types={"py": True, "txt": True},
        include_all=False,
    )
    assert ctx.aggregate_tokens > 0
    assert len(ctx.files) == 2
    assert ctx.by_ext["py"] and ctx.by_ext["txt"]
