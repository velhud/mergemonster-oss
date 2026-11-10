from __future__ import annotations

from codemerge.llm import _chunking as chunking


class StubFP:
    def count_tokens(self, text: str) -> int:
        return len(text)


def test_chunk_text_preserves_coverage_and_budget():
    fp = StubFP()
    text = "line one\nline two\nline three\nline four\n"
    chunks, info = chunking._chunk_text(fp, text, chunk_tokens=10, file_path="sample.txt")
    assert info["coverage_ok"] is True
    assert "".join(chunks) == text
    assert all(len(chunk) <= 10 for chunk in chunks)


def test_chunk_text_handles_unicode():
    fp = StubFP()
    text = "naive cafe\nnaive cafe\n" + "cafe\n"
    # Inject a Unicode code point to ensure non-ASCII coverage works.
    text = text.replace("cafe", "caf\u00e9")
    chunks, info = chunking._chunk_text(fp, text, chunk_tokens=8, file_path="sample.py")
    assert info["coverage_ok"] is True
    assert "".join(chunks) == text
    assert all(len(chunk) <= 8 for chunk in chunks)
