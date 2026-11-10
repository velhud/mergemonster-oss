from __future__ import annotations


def test_git_helpers_without_repo(tmp_path):
    from codemerge.filemerger import git as git_mod

    root = tmp_path / "proj"
    root.mkdir()

    assert git_mod.git_files(str(root)) == []
    assert git_mod.git_vendored_set(str(root), ["src/main.py"]) == set()
