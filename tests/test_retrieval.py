import os
import sys
import unittest
import tempfile
from pathlib import Path
import types
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from codemerge.core.tokens import get_encoding

from codemerge.filemerger import FileProcessor  # noqa: E402
from codemerge.llm import _embeddings  # noqa: E402
from codemerge.prune.symbol_index import build_symbol_index  # noqa: E402
from codemerge.llm.embed_types import DEFAULT_OPENAI_EMBED_MODEL  # noqa: E402
from codemerge.llm import retrieval as llm_retrieval  # noqa: E402
from codemerge.prune.retrieval import (
    rank_candidates,
    expand_neighbors,
)  # noqa: E402

encoder = get_encoding()


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _scan_index(self):
        fp = FileProcessor()
        ctx = fp.scan_directory(
            directory=self.root,
            file_types={},
            include_all=True,
            sort_by="name",
            sort_descending=False,
            ignore_binary=False,
            max_file_size_mb=0.0,
        )
        index = build_symbol_index(ctx, fp)
        return fp, index

    def test_neighbor_expansion(self):
        main = (
            "from .util import util_func\n\n"
            "def spurious():\n    return 0\n\n"
            "def helper():\n    return util_func()\n\n"
            "def target():\n"
            "    # spurious\n"
            "    msg = 'spurious call'\n"
            "    return helper() + util_func()\n"
        )
        util = "def util_func():\n    return 1\n"
        with open(os.path.join(self.root, "main.py"), "w", encoding="utf-8") as f:
            f.write(main)
        with open(os.path.join(self.root, "util.py"), "w", encoding="utf-8") as f:
            f.write(util)
        fp, index = self._scan_index()
        cands = rank_candidates("target", index)
        self.assertEqual(len(cands), 1)
        expanded = expand_neighbors(cands, index)
        found = {(c.path, c.qualname) for c in expanded}
        main_path = os.path.join(self.root, "main.py")
        util_path = os.path.join(self.root, "util.py")
        self.assertIn((main_path, "helper"), found)
        self.assertIn((util_path, None), found)
        self.assertNotIn((main_path, "spurious"), found)

    def test_embedding_ranking_changes_score(self):
        code = (
            "def foo():\n    \"do foo\"\n    pass\n\n"
            "def bar():\n    \"does bar stuff\"\n    pass\n"
        )
        with open(os.path.join(self.root, "mod.py"), "w", encoding="utf-8") as f:
            f.write(code)
        fp, index = self._scan_index()
        cands_lex = rank_candidates("bar stuff", index, use_embeddings=False)

        def fake_get(text, provider, key, **kwargs):
            if "bar" in text:
                return [1.0, 0.0]
            return [0.0, 1.0]

        with mock.patch.object(llm_retrieval, "get_embedding", side_effect=fake_get):
            cands_emb = rank_candidates(
                "bar stuff",
                index,
                use_embeddings=True,
                embedding_provider="openai",
            )

        self.assertEqual(cands_lex[0].qualname, "bar")
        self.assertEqual(cands_emb[0].qualname, "bar")
        self.assertNotEqual(cands_lex[0].score, cands_emb[0].score)
        self.assertEqual(cands_emb[0].reason, "hybrid")

    def test_skip_embeddings_with_none_provider(self):
        code = "def foo():\n    pass\n"
        with open(os.path.join(self.root, "a.py"), "w", encoding="utf-8") as f:
            f.write(code)
        fp, index = self._scan_index()
        with mock.patch.object(llm_retrieval, "get_embedding") as ge:
            cands = rank_candidates(
                "foo",
                index,
                use_embeddings=True,
                embedding_provider="none",
            )
        self.assertEqual(ge.call_count, 0)
        self.assertEqual(cands[0].reason, "lexical")

    def test_cached_embeddings_are_reused(self):
        code = "def foo():\n    pass\n"
        path = os.path.join(self.root, "m.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        fp, index = self._scan_index()
        calls = []

        class DummyEmb:
            def create(self, **kwargs):
                assert kwargs.get("model") == DEFAULT_OPENAI_EMBED_MODEL
                calls.append(kwargs)
                return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[1.0])])

        class DummyClient:
            def __init__(self):
                self.embeddings = DummyEmb()

        dbdir = tempfile.TemporaryDirectory()
        dbpath = Path(dbdir.name) / "emb.db"
        with (
            mock.patch.object(_embeddings, "openai_available", return_value=True),
            mock.patch.object(_embeddings, "get_openai_client", lambda: DummyClient()),
            mock.patch.object(_embeddings, "EMBED_DB", dbpath),
        ):
            rank_candidates("foo", index, use_embeddings=True, embedding_provider="openai")
            rank_candidates("foo", index, use_embeddings=True, embedding_provider="openai")
        dbdir.cleanup()
        self.assertEqual(len(calls), 2)


    def test_embedding_cache_separates_dimensions(self):
        code = "def foo():\n    pass\n"
        path = os.path.join(self.root, "d.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        fp, index = self._scan_index()
        calls: list[dict] = []

        class DummyEmb:
            def create(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[1.0])])

        class DummyClient:
            def __init__(self):
                self.embeddings = DummyEmb()

        dbdir = tempfile.TemporaryDirectory()
        dbpath = Path(dbdir.name) / "emb_with_dims.db"
        with (
            mock.patch.object(_embeddings, "openai_available", return_value=True),
            mock.patch.object(_embeddings, "get_openai_client", lambda: DummyClient()),
            mock.patch.object(_embeddings, "EMBED_DB", dbpath),
        ):
            rank_candidates(
                "foo",
                index,
                use_embeddings=True,
                embedding_provider="openai",
                openai_embedding_model=DEFAULT_OPENAI_EMBED_MODEL,
                openai_embedding_dimensions=256,
            )
            rank_candidates(
                "foo",
                index,
                use_embeddings=True,
                embedding_provider="openai",
                openai_embedding_model=DEFAULT_OPENAI_EMBED_MODEL,
                openai_embedding_dimensions=512,
            )
        dbdir.cleanup()
        dims_reported = [payload.get("dimensions") for payload in calls]
        self.assertIn(256, dims_reported)
        self.assertIn(512, dims_reported)
        self.assertGreaterEqual(len(calls), 4)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
