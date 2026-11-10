import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from codemerge.core.tokens import get_encoding

from codemerge.filemerger import FileProcessor  # noqa: E402
from codemerge.prune.symbol_index import build_symbol_index  # noqa: E402
from codemerge.prune.retrieval import Candidate  # noqa: E402
from codemerge.prune.pruner import build_prune_plan, write_pruned_merge  # noqa: E402
from codemerge.prune.verifier import validate_and_repair  # noqa: E402

encoder = get_encoding()


def test_logging_with_repairs():
    with tempfile.TemporaryDirectory() as root:
        main_path = Path(root) / "main.py"
        helper_path = Path(root) / "helper.py"
        main_path.write_text("def a():\n    return helper_func()\n")
        helper_path.write_text("def helper_func():\n    return 42\n")

        fp = FileProcessor()
        ctx = fp.scan_directory(
            directory=root,
            file_types={},
            include_all=True,
            sort_by="name",
            sort_descending=False,
            ignore_binary=False,
            max_file_size_mb=0.0,
        )
        index = build_symbol_index(ctx, fp)

        cands = [Candidate(str(main_path), "def", "a", 1, 2, 1.0, "manual")]
        plan = build_prune_plan("goal", index, cands, fp, budget_tokens=1000)
        plan, logs = validate_and_repair(plan, root, index, fp)
        out_path = os.path.join(root, "out.txt")
        write_pruned_merge(plan, root, out_path, fp, budget_tokens=1000, log_entries=logs)
        log_file = out_path + ".log"
        assert os.path.exists(log_file)
        content = Path(log_file).read_text()
        lines = [ln for ln in content.splitlines() if ln]
        assert "manual" in content
        assert "repair-undefined" in content
        roles = [ln.split("\t")[-1] for ln in lines]
        assert "seed" in roles
        assert "repair" in roles
        assert any("added" in ln for ln in lines)
        merged = Path(out_path).read_text()
        assert "# why: manual" in merged
        assert "# why: repair-undefined" in merged
