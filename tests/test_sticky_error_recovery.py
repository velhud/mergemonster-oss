from __future__ import annotations

import os

from codemerge.llm.summarize import file_prompt_fingerprint
from codemerge.llm.config import DEFAULT_GROQ_SUMMARY_MODEL
from codemerge.summarizer.shared import orchestrator as orch
from codemerge.summarizer.shared.summary_cache import SummaryCache


class StubFP:
    def is_binary_file(self, _path: str) -> bool:
        return False

    def read_file_content(self, path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except Exception:
            return None


def test_sticky_error_rows_retry_even_without_auto_refresh(tmp_path, monkeypatch):
    file_path = tmp_path / "demo.py"
    file_path.write_text("print('x')\n", encoding="utf-8")
    cache_path = tmp_path / "summary_cache.db"

    cfg = {
        "summary_cache_db_path": str(cache_path),
        "summary_prompt_version": 5,
        "summary_change_strategy": "mtime_size",
        "auto_summarize_missing": True,
        "auto_refresh_summaries": False,
    }

    prompt_fp = file_prompt_fingerprint("short", cfg["summary_prompt_version"], model=DEFAULT_GROQ_SUMMARY_MODEL)
    st = os.stat(file_path)
    cache = SummaryCache(cache_path)
    cache.put_error(
        str(file_path),
        model=DEFAULT_GROQ_SUMMARY_MODEL,
        kind="short",
        prompt_fp=prompt_fp,
        mtime=float(st.st_mtime),
        size=int(st.st_size),
    )
    cache.close()

    def stub_fetch(paths, *_args, **_kwargs):
        summaries = {paths[0]: "prints"}
        return summaries, []

    monkeypatch.setattr(orch, "fetch_ai_summaries", stub_fetch)

    out, errors = orch.ensure_summaries(
        [str(file_path)],
        "short",
        StubFP(),
        cfg,
        progress_cb=None,
        force=False,
        root=str(tmp_path),
        strict_required=True,
    )

    assert errors == []
    assert out[str(file_path)] is not None

    cache = SummaryCache(cache_path)
    row = cache.get(str(file_path), DEFAULT_GROQ_SUMMARY_MODEL, "short", prompt_fp)
    cache.close()
    assert row is not None
    assert row["status"] == "ok"
