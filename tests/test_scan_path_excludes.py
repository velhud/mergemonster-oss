import os

from codemerge.filemerger import FileProcessor


def test_scan_path_excludes(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    ignore_name = root / "ignore_name"
    ignore_name.mkdir()
    (ignore_name / "a.txt").write_text("ignore\n", encoding="utf-8")

    abs_dir = root / "abs_dir"
    abs_dir.mkdir()
    (abs_dir / "abs.txt").write_text("ignore\n", encoding="utf-8")

    nested = root / "sub" / "nested"
    nested.mkdir(parents=True)
    (nested / "nested.txt").write_text("ignore\n", encoding="utf-8")

    other = root / "sub" / "other"
    other.mkdir(parents=True)
    (other / "other.txt").write_text("keep\n", encoding="utf-8")

    keep = root / "keep"
    keep.mkdir()
    (keep / "ok.txt").write_text("ok\n", encoding="utf-8")
    (keep / "skip_rel.txt").write_text("skip\n", encoding="utf-8")

    (root / "skip_name.txt").write_text("skip\n", encoding="utf-8")
    (root / "abs_file.txt").write_text("skip\n", encoding="utf-8")
    (root / "sub" / "skip_name.txt").write_text("skip\n", encoding="utf-8")

    fp = FileProcessor()
    fp.set_excluded_dirs(
        {
            "ignore_name",
            str(abs_dir),
            os.path.join("sub", "nested"),
        }
    )
    fp.set_excluded_files(
        {
            "skip_name.txt",
            str(root / "abs_file.txt"),
            os.path.join("keep", "skip_rel.txt"),
        }
    )

    ctx = fp.scan_directory(str(root), file_types={}, include_all=True)
    rels = {meta.rel_path.as_posix() for meta in ctx.files}

    assert "keep/ok.txt" in rels
    assert "sub/other/other.txt" in rels
    assert "ignore_name/a.txt" not in rels
    assert "abs_dir/abs.txt" not in rels
    assert "sub/nested/nested.txt" not in rels
    assert "skip_name.txt" not in rels
    assert "sub/skip_name.txt" not in rels
    assert "abs_file.txt" not in rels
    assert "keep/skip_rel.txt" not in rels
