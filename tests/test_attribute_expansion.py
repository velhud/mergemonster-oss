import tempfile
from pathlib import Path
import unittest

from codemerge.core.tokens import get_encoding

from codemerge.filemerger import FileProcessor
from codemerge.prune.symbol_index import build_symbol_index
from codemerge.prune.retrieval import rank_candidates, expand_neighbors

encoder = get_encoding()


class AttributeExpansionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _scan(self):
        fp = FileProcessor()
        ctx = fp.scan_directory(
            directory=str(self.root),
            file_types={},
            include_all=True,
            sort_by="name",
            sort_descending=False,
            ignore_binary=False,
            max_file_size_mb=0.0,
        )
        index = build_symbol_index(ctx, fp)
        return fp, index

    def test_attribute_based_neighbor_expansion(self):
        (self.root / "mod_a.py").write_text("def helper():\n    return 1\n")
        (self.root / "mod_b.py").write_text(
            "import mod_a\nimport math\n\n"
            "def caller():\n"
            "    mod_a.helper()\n"
            "    math.sqrt(4)\n"
            "    return 0\n"
        )
        fp, index = self._scan()
        cands = rank_candidates("caller", index)
        expanded = expand_neighbors(cands, index)
        found = {(c.path, c.qualname) for c in expanded}
        self.assertIn((str(self.root / "mod_a.py"), "helper"), found)
        # ensure unrelated modules like math are not added
        self.assertTrue(all(not p.endswith("math.py") for p, _ in found))
        self.assertTrue(all(q != "sqrt" for _, q in found if q))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
