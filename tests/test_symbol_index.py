import time
from pathlib import Path

from codemerge.core.tokens import get_encoding

from codemerge.prune.symbol_index import build_symbol_index
from codemerge.models import Category, FileMeta, ScanContext

encoder = get_encoding()


def _make_ctx(root: Path) -> ScanContext:
    files: list[FileMeta] = []
    aggregate_tokens = 0
    aggregate_bytes = 0
    by_ext: dict[str, list[int]] = {}
    by_category: dict[Category, list[int]] = {}
    for path in sorted(root.glob("*.py")):
        stat = path.stat()
        tokens = len(encoder.encode(path.read_text(), disallowed_special=()))
        fm = FileMeta(
            abs_path=path,
            rel_path=path.relative_to(root),
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            token_count=tokens,
            ext="py",
            category=Category.BACKEND,
        )
        files.append(fm)
        aggregate_tokens += tokens
        aggregate_bytes += stat.st_size
        by_ext.setdefault("py", []).append(len(files) - 1)
        by_category.setdefault(Category.BACKEND, []).append(len(files) - 1)
    return ScanContext(root, files, aggregate_tokens, aggregate_bytes, by_ext, by_category)


def test_incremental_build(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    c = tmp_path / "c.py"

    a.write_text("def a():\n    pass\n")
    b.write_text("def b():\n    pass\n")
    c.write_text("def c():\n    pass\n")

    ctx = _make_ctx(tmp_path)
    index = build_symbol_index(ctx, object())

    pa, pb, pc = map(str, [a, b, c])
    index[pa]["tokens"] = 111
    index[pa]["sentinel"] = True
    index[pb]["tokens"] = 222
    index[pb]["sentinel"] = True
    index[pc]["tokens"] = 333
    index[pc]["sentinel"] = True

    time.sleep(0.01)
    b.write_text("def b():\n    return 1\n")
    c.unlink()

    ctx2 = _make_ctx(tmp_path)
    updated = build_symbol_index(ctx2, object(), index)

    assert pa in updated
    assert updated[pa]["tokens"] == 111
    assert updated[pa]["sentinel"] is True

    assert pb in updated
    assert updated[pb]["tokens"] != 222
    assert "sentinel" not in updated[pb]

    assert pc not in updated
