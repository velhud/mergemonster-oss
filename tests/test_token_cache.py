import time

from codemerge.filemerger import FileProcessor


def test_token_cache_roundtrip(tmp_path):
    fp = FileProcessor()
    p = tmp_path / "a.py"
    p.write_text("print('x')")

    first = fp.file_token_count(str(p))
    assert first > 0

    cached = fp.file_token_count(str(p))
    assert cached == first

    time.sleep(1.1)
    p.write_text("print('" + "x" * 1000 + "')")
    updated = fp.file_token_count(str(p))
    assert updated != first


def test_token_cache_persists_between_instances(tmp_path):
    fp = FileProcessor()
    p = tmp_path / "b.py"
    p.write_text("print('persist')")

    first = fp.file_token_count(str(p))
    assert first > 0
    # Ensure connection closed and data committed
    del fp

    fp2 = FileProcessor()
    cached = fp2.file_token_count(str(p))
    assert cached == first
