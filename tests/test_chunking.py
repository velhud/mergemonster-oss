import unittest

from codemerge.llm import _chunking as chunking


class DummyFP:
    def count_tokens(self, text: str) -> int:
        return max(len(text) // 4, 1)


class ChunkingTests(unittest.TestCase):
    def test_chunk_text_preserves_coverage(self) -> None:
        fp = DummyFP()
        text = "".join(["line %d\n" % i for i in range(500)])
        chunks, debug = chunking._chunk_text(
            fp,
            text,
            file_path="notes.txt",
            chunk_tokens=80,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(debug.get("coverage_ok"))
        self.assertEqual("".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
