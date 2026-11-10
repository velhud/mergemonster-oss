import sys
import time
import os

from codemerge.core.tokens import get_encoding

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import codemerge.agent.core as agent  # noqa: E402
import codemerge.prune.symbol_index as symbol_index  # noqa: E402

encoder = get_encoding()


def _stub_dependencies(monkeypatch, tmp_path):
    cache_dir = tmp_path.parent / "cache"
    cache_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("CODEMERGE_HOME", str(cache_dir / "home"))
    monkeypatch.setattr(symbol_index, "CACHE_FILE", cache_dir / "symbols.json")
    monkeypatch.setattr(agent, "select_spans_with_llm", lambda *a, **k: [])
    monkeypatch.setattr(agent, "rank_candidates", lambda *a, **k: [])
    monkeypatch.setattr(agent, "expand_neighbors", lambda c, i: c)
    monkeypatch.setattr(agent, "build_prune_plan", lambda *a, **k: {})
    monkeypatch.setattr(agent, "validate_and_repair", lambda p, r, i, fp, max_passes=3: (p, []))
    monkeypatch.setattr(
        agent,
        "write_pruned_merge",
        lambda *a, **k: (
            "out",
            None,
            {"kept_defs": 0, "kept_files": 0, "tokens": 0},
            {"kept_defs": 0, "kept_files": 0, "tokens": 0},
        ),
    )
    monkeypatch.setattr(agent, "groq_available", lambda: False)


def test_prune_by_goal_reuses_cached_index(tmp_path, monkeypatch):
    _stub_dependencies(monkeypatch, tmp_path)

    call_count = 0
    orig_build = agent.build_symbol_index

    def spy(ctx, fp, existing=None):
        nonlocal call_count
        call_count += 1
        return orig_build(ctx, fp, existing)

    monkeypatch.setattr(agent, "build_symbol_index", spy)

    src = tmp_path / "a.py"
    src.write_text("def a():\n    return 1\n")

    cfg = {"include_all": True}
    agent.prune_by_goal("goal", str(tmp_path), str(tmp_path / "out1.txt"), config=cfg)
    agent.prune_by_goal("goal", str(tmp_path), str(tmp_path / "out2.txt"), config=cfg)

    assert call_count == 1
    assert symbol_index.CACHE_FILE.exists()
    if symbol_index.CACHE_FILE.exists():
        symbol_index.CACHE_FILE.unlink()


def test_prune_by_goal_rebuilds_when_files_change(tmp_path, monkeypatch):
    _stub_dependencies(monkeypatch, tmp_path)

    call_count = 0
    orig_build = agent.build_symbol_index

    def spy(ctx, fp, existing=None):
        nonlocal call_count
        call_count += 1
        return orig_build(ctx, fp, existing)

    monkeypatch.setattr(agent, "build_symbol_index", spy)

    src = tmp_path / "a.py"
    src.write_text("def a():\n    return 1\n")

    cfg = {"include_all": True}
    agent.prune_by_goal("goal", str(tmp_path), str(tmp_path / "out1.txt"), config=cfg)
    index1 = symbol_index.load_symbol_index(str(tmp_path))
    tokens1 = index1[str(src)]["tokens"]

    time.sleep(0.01)
    src.write_text("def a():\n    return 1 + 1\n")

    agent.prune_by_goal("goal", str(tmp_path), str(tmp_path / "out2.txt"), config=cfg)
    index2 = symbol_index.load_symbol_index(str(tmp_path))
    tokens2 = index2[str(src)]["tokens"]

    assert call_count == 2
    assert tokens2 != tokens1
    if symbol_index.CACHE_FILE.exists():
        symbol_index.CACHE_FILE.unlink()
