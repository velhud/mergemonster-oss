import os
import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from codemerge.core.tokens import get_encoding

from codemerge.filemerger import FileProcessor  # noqa: E402
from codemerge.prune.utils import slice_lines  # noqa: E402
from codemerge.prune.symbol_index import build_symbol_index  # noqa: E402
from codemerge.prune.retrieval import rank_candidates, Candidate  # noqa: E402
from codemerge.prune.pruner import (
    build_prune_plan,
    write_pruned_merge,
    write_pruned_tree,
    delete_defs,
    Plan,
)  # noqa: E402
from codemerge.agent import prune_by_goal  # noqa: E402

encoder = get_encoding()


class PrunerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _scan_index(self):
        fp = FileProcessor()
        ctx = fp.scan_directory(directory=self.root, file_types={}, include_all=True,
                                sort_by="name", sort_descending=False,
                                ignore_binary=False, max_file_size_mb=0.0)
        index = build_symbol_index(ctx, fp)
        return fp, index

    def test_method_pulls_class_and_constant(self):
        filler = "\n".join(f"x{i} = {i}" for i in range(600))
        src = f"{filler}\nCONST = 1\n\nclass Outer:\n    class Inner:\n        def target(self):\n            return CONST\n"
        path = os.path.join(self.root, "mod.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        fp, index = self._scan_index()
        cands = rank_candidates("target", index)
        plan = build_prune_plan("target", index, cands, fp, budget_tokens=5000)
        quals = {sp["qualname"] for sp in plan.spans}
        self.assertIn("Outer.Inner", quals)
        self.assertIn("CONST", quals)

    def test_overflow_pack_created(self):
        code_a = "def a():\n    return 1\n"
        code_b = "def b():\n    return 2\n"
        seed = os.path.join(self.root, "seed.py")
        extra = os.path.join(self.root, "extra.py")
        with open(seed, "w", encoding="utf-8") as f:
            f.write(code_a)
        with open(extra, "w", encoding="utf-8") as f:
            f.write(code_b)
        fp, index = self._scan_index()
        cands = [
            Candidate(seed, "file", None, None, None, 1.0, "manual"),
            Candidate(extra, "file", None, None, None, 0.5, "neighbor"),
        ]
        plan = build_prune_plan("goal", index, cands, fp, budget_tokens=50)
        out_path = os.path.join(self.root, "out.txt")
        main, overflow, stats_a, stats_b = write_pruned_merge(
            plan,
            self.root,
            out_path,
            fp,
            budget_tokens=(fp.file_token_count(seed) or 0) + 1,
        )
        self.assertTrue(os.path.exists(main))
        self.assertTrue(os.path.exists(overflow))
        with open(main, "r", encoding="utf-8") as f:
            content_main = f.read()
        with open(overflow, "r", encoding="utf-8") as f:
            content_over = f.read()
        self.assertIn("seed.py", content_main)
        self.assertIn("extra.py", content_over)

    def test_compact_mode_respects_budget(self):
        code_high = "def a():\n    return 1\n"
        code_mid = "def b():\n    return 1\n"
        code_low = "def c():\n    return 1\n"
        high = os.path.join(self.root, "high.py")
        mid = os.path.join(self.root, "mid.py")
        low = os.path.join(self.root, "low.py")
        for path, src in [(high, code_high), (mid, code_mid), (low, code_low)]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
        fp, index = self._scan_index()
        cands = [
            Candidate(high, "file", None, None, None, 3.0, "manual"),
            Candidate(mid, "file", None, None, None, 2.0, "manual"),
            Candidate(low, "file", None, None, None, 1.0, "neighbor"),
        ]
        budget = sum(fp.file_token_count(p) or 0 for p in [high, mid])
        plan = build_prune_plan("goal", index, cands, fp, budget_tokens=50)
        out_path = os.path.join(self.root, "cmp.txt")
        main, overflow, stats_a, _ = write_pruned_merge(
            plan,
            self.root,
            out_path,
            fp,
            budget_tokens=budget,
            mode="compact",
        )
        self.assertTrue(os.path.exists(main))
        self.assertIsNone(overflow)
        overflow_path = os.path.join(self.root, "cmp_overflow.txt")
        self.assertFalse(os.path.exists(overflow_path))
        with open(main, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("high.py", content)
        self.assertIn("mid.py", content)
        self.assertNotIn("low.py", content)
        self.assertLessEqual(stats_a["tokens"], budget)

        # Safe mode should also overflow with the low-scoring file included
        plan2 = build_prune_plan("goal", index, cands, fp, budget_tokens=50)
        safe_path = os.path.join(self.root, "safe.txt")
        main2, overflow2, stats_main, stats_over = write_pruned_merge(
            plan2,
            self.root,
            safe_path,
            fp,
            budget_tokens=budget,
            mode="safe",
        )
        self.assertTrue(os.path.exists(main2))
        self.assertTrue(os.path.exists(overflow2))
        with open(main2, "r", encoding="utf-8") as f:
            content_main = f.read()
        with open(overflow2, "r", encoding="utf-8") as f:
            content_over = f.read()
        combined = content_main + content_over
        self.assertIn("high.py", combined)
        self.assertIn("mid.py", combined)
        self.assertIn("low.py", combined)
        self.assertLessEqual(stats_main["tokens"], budget)

    def test_agent_compact_mode_stats(self):
        code = "def a():\n    return 1\n"
        path = os.path.join(self.root, "main.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        out_file = os.path.join(self.root, "agent.txt")
        cfg = {
            "file_types": {"py": True},
            "include_all": True,
            "prune_token_budget": 5,
            "prune_mode": "compact",
        }
        stats = prune_by_goal("a", self.root, out_file, cfg)
        self.assertIsNone(stats["overflow"])
        self.assertEqual(stats["overflow_defs"], 0)
        self.assertEqual(stats["overflow_files"], 0)
        self.assertEqual(stats["overflow_tokens"], 0)

    def test_repairs_remain_in_main_pack(self):
        seed_code = "def seed():\n    return 1\n"
        repair_code = "def fix():\n    return 2\n"
        extra_code = "def extra():\n    return 3\n"
        seed = os.path.join(self.root, "seed.py")
        repair = os.path.join(self.root, "repair.py")
        extra = os.path.join(self.root, "extra.py")
        for path, src in [(seed, seed_code), (repair, repair_code), (extra, extra_code)]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
        fp, index = self._scan_index()
        cands = [
            Candidate(seed, "file", None, None, None, 1.0, "manual"),
            Candidate(repair, "file", None, None, None, 0.9, "repair missing dep"),
            Candidate(extra, "file", None, None, None, 0.5, "neighbor"),
        ]
        plan = build_prune_plan("goal", index, cands, fp, budget_tokens=50)
        out_path = os.path.join(self.root, "repair.txt")
        budget = fp.file_token_count(seed) or 0
        main, overflow, *_ = write_pruned_merge(
            plan,
            self.root,
            out_path,
            fp,
            budget_tokens=budget,
            mode="safe",
        )
        with open(main, "r", encoding="utf-8") as f:
            content_main = f.read()
        self.assertIn("seed.py", content_main)

    def test_delete_defs_preserves_class(self):
        src = (
            "class A:\n"
            "    def keep(self):\n"
            "        pass\n\n"
            "    def drop(self):\n"
            "        pass\n"
        )
        path = os.path.join(self.root, "mod.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        removed, patch = delete_defs(path, ["A.drop"])
        self.assertIn("A.drop", removed)
        self.assertIsNotNone(patch)
        self.assertIn("def keep", patch.updated)
        self.assertIn("class A", patch.updated)
        self.assertNotIn("drop(self)", patch.updated)

    def test_write_pruned_merge_includes_why(self):
        file_path = os.path.join(self.root, "full.py")
        span_path = os.path.join(self.root, "span.py")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("x=1\n")
        with open(span_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        fp = FileProcessor()
        plan = Plan(
            spans=[
                {
                    "path": span_path,
                    "start": 1,
                    "end": 2,
                    "qualname": "foo",
                    "kind": "def",
                    "tokens": 1,
                    "score": 1.0,
                    "reason": "seed",
                    "role": "seed",
                }
            ],
            full_files=[file_path],
            dropped=[],
            file_tokens={file_path: 0},
            order=[
                {
                    "type": "span",
                    "path": span_path,
                    "start": 1,
                    "end": 2,
                    "qualname": "foo",
                    "kind": "def",
                    "tokens": 1,
                    "score": 1.0,
                    "reason": "seed",
                    "role": "seed",
                },
                {
                    "type": "file",
                    "path": file_path,
                    "tokens": 0,
                    "score": 1.0,
                    "reason": "neighbor",
                    "role": "neighbor",
                },
            ],
        )
        out = os.path.join(self.root, "merged.txt")
        write_pruned_merge(plan, self.root, out, fp, budget_tokens=1000)
        content = Path(out).read_text()
        self.assertIn("# why: seed", content)
        self.assertIn("# why: neighbor", content)

    def test_seeds_exceed_budget_retained(self):
        big1 = "def a():\n" + "    return 1\n" * 200
        big2 = "def b():\n" + "    return 1\n" * 200
        neigh_code = "def n():\n    return 1\n"
        seed1 = os.path.join(self.root, "s1.py")
        seed2 = os.path.join(self.root, "s2.py")
        neigh = os.path.join(self.root, "n.py")
        for path, src in [(seed1, big1), (seed2, big2), (neigh, neigh_code)]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
        fp, index = self._scan_index()
        cands = [
            Candidate(seed1, "file", None, None, None, 1.0, "lexical"),
            Candidate(seed2, "file", None, None, None, 0.9, "hybrid"),
            Candidate(neigh, "file", None, None, None, 0.1, "neighbor"),
        ]
        plan = build_prune_plan("goal", index, cands, fp, budget_tokens=50)
        budget = (fp.file_token_count(seed1) or 0) + 10

        out_a = os.path.join(self.root, "seeda.txt")
        main, overflow, stats_a, _ = write_pruned_merge(
            plan, self.root, out_a, fp, budget_tokens=budget, mode="safe"
        )
        with open(main, "r", encoding="utf-8") as f:
            main_content = f.read()
        self.assertIn("s1.py", main_content)
        self.assertIn("s2.py", main_content)
        if overflow:
            with open(overflow, "r", encoding="utf-8") as f:
                over_content = f.read()
            self.assertIn("n.py", over_content)
            self.assertNotIn("s1.py", over_content)
            self.assertNotIn("s2.py", over_content)
        self.assertGreater(stats_a["tokens"], budget)

        out_b = os.path.join(self.root, "seedb.txt")
        main2, overflow2, stats_c, _ = write_pruned_merge(
            plan, self.root, out_b, fp, budget_tokens=budget, mode="compact"
        )
        with open(main2, "r", encoding="utf-8") as f:
            main2_content = f.read()
        self.assertIn("s1.py", main2_content)
        self.assertIn("s2.py", main2_content)
        if overflow2:
            with open(overflow2, "r", encoding="utf-8") as f:
                over2 = f.read()
            self.assertIn("n.py", over2)
            self.assertNotIn("s1.py", over2)
            self.assertNotIn("s2.py", over2)
        self.assertGreater(stats_c["tokens"], budget)

    def test_write_pruned_tree_truncates_spans(self):
        filler = "\n".join(f"x{i} = {i}" for i in range(600))
        src = (
            f"CONST = 1\n\n{filler}\n\n"
            "def keep():\n    return CONST\n\n"
            "def drop():\n    return 0\n"
        )
        path = os.path.join(self.root, "mod.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        fp, index = self._scan_index()
        cands = rank_candidates("keep", index)
        plan = build_prune_plan("keep", index, cands, fp, budget_tokens=5000)
        out_dir = os.path.join(self.root, "tree")
        write_pruned_tree(plan, self.root, out_dir, fp)
        dest = os.path.join(out_dir, "mod.py")
        with open(dest, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("CONST = 1\n", content)
        self.assertIn("def keep():\n    return CONST\n", content)
        self.assertNotIn("def drop()", content)
        self.assertTrue(content.endswith("\n"))

    def test_merge_selected_spans_regression(self):
        path_a = os.path.join(self.root, "a.py")
        path_b = os.path.join(self.root, "b.py")
        with open(path_a, "w", encoding="utf-8") as f:
            f.write("def alpha():\n    pass\n\n")
        with open(path_b, "w", encoding="utf-8") as f:
            f.write("def beta():\n\n    return 1\n")

        spans = [
            {"path": path_a, "start": 1, "end": 3},
            {"path": path_b, "start": 1, "end": 3},
        ]

        out_path = os.path.join(self.root, "spans.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("HEADER\n")

        fp = FileProcessor()
        fp.merge_selected_spans(
            spans,
            self.root,
            out_path,
            "======= START OF FILE: {file_display} =======",
            "======= END OF FILE: {file_display} =======",
            append=True,
            enumerate_lines=True,
            remove_empty=True,
        )

        expected = "HEADER\n"
        for sp in spans:
            segment = slice_lines(sp["path"], sp["start"], sp["end"])
            lines = [ln for ln in segment.splitlines() if ln.strip()]
            width = len(str(len(lines))) if lines else 1
            lines = [f"{i+1:>{width}}: {ln}" for i, ln in enumerate(lines)]
            segment = "\n".join(lines)
            if segment and not segment.endswith("\n"):
                segment += "\n"
            rel = os.path.relpath(sp["path"], self.root)
            display = f"{rel}  # lines {sp['start']}-{sp['end']}"
            expected += (
                f"======= START OF FILE: '{display}' =======\n"
                f"{segment}"
                f"======= END OF FILE: '{display}' =======\n\n"
            )

        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, expected)

        fp.merge_selected_spans(
            [],
            self.root,
            out_path,
            "S", "E",
            append=True,
        )
        with open(out_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), expected)

    def test_span_tokens_threshold_behavior(self):
        big_path = os.path.join(self.root, "big.py")
        small_path = os.path.join(self.root, "small.py")

        def make_func(name: str, lines: int) -> str:
            body = "".join("    x = 1\n" for _ in range(lines))
            return f"def {name}():\n{body}\n"

        with open(big_path, "w", encoding="utf-8") as f:
            src = "".join(make_func(f"f{i}", 100) for i in range(5))
            f.write(src)
        with open(small_path, "w", encoding="utf-8") as f:
            src = make_func("a", 10) + make_func("b", 10)
            f.write(src)

        fp, index = self._scan_index()
        big_cands = [Candidate(big_path, "def", f"f{i}", None, None, 1.0, "goal") for i in range(5)]
        small_cands = [
            Candidate(small_path, "def", "a", None, None, 1.0, "goal"),
            Candidate(small_path, "def", "b", None, None, 1.0, "goal"),
        ]

        plan_big = build_prune_plan("g", index, big_cands, fp, budget_tokens=10_000)
        self.assertNotIn(big_path, plan_big.full_files)
        spans_big = [sp for sp in plan_big.spans if sp["path"] == big_path]
        self.assertEqual(len(spans_big), 5)

        plan_small = build_prune_plan("g", index, small_cands, fp, budget_tokens=10_000)
        self.assertIn(small_path, plan_small.full_files)
        spans_small = [sp for sp in plan_small.spans if sp["path"] == small_path]
        self.assertEqual(spans_small, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
