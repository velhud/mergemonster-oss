from codemerge.filemerger import FileProcessor


def test_special_token_handling(tmp_path):
    fp = FileProcessor()
    p = tmp_path / "special.txt"
    p.write_text("foo <|endoftext|> bar")
    count = fp.file_token_count(str(p))
    assert isinstance(count, int)
    assert count is not None
