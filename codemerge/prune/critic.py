from __future__ import annotations
from typing import Dict, List, Optional

from ..llm.prune import drop_spans_with_llm


def _span_table(index: Dict[str, Dict]) -> Dict[str, List[dict]]:
    """Return {path: [{qualname,start,end,kind}..]} using index defs."""
    tbl: Dict[str, List[dict]] = {}
    for p, info in index.items():
        lst = []
        for d in info.get("defs", []) or []:
            lst.append({
                "qualname": d.get("qualname"),
                "start": d.get("start"),
                "end": d.get("end"),
                "kind": d.get("kind"),
            })
        tbl[p] = lst
    return tbl


def _map_lines_to_quals(path: str, lines: List[int], spans: List[dict]) -> List[str]:
    q: list[str] = []
    for s in spans:
        if s["start"] is None or s["end"] is None:
            continue
        if any(s["start"] <= ln <= s["end"] for ln in lines):
            q.append(s["qualname"])
    return sorted(set(q))


def refine(
    goal: str,
    tier0: List[dict],
    tier1: List[dict],
    trace: Dict,
    index: Dict[str, Dict],
    *,
    client: Optional[object],
    cfg: Dict,
) -> Dict:
    """
    Return actions JSON: {add_spans:[{path,qualname}], add_files:[path], drop_spans:[{path,qualname}]}
    Heuristic + optional LLM.
    """
    actions = {"add_spans": [], "add_files": [], "drop_spans": []}
    tbl = _span_table(index)
    executed = trace.get("executed", {}) or {}
    errors = trace.get("errors", []) or []

    keep_set = {(s["path"], s["qualname"]) for s in tier0}
    # 1) Add spans covered by execution but not in keep_set
    for p, lines in executed.items():
        spans = tbl.get(p, [])
        quals = _map_lines_to_quals(p, lines, spans)
        for q in quals:
            if (p, q) not in keep_set and q:
                actions["add_spans"].append({"file_path": p, "qualname": q})

    # 2) Optional LLM pass to drop clearly irrelevant spans (disabled by default)
    if cfg.get("critic_drop_with_llm", False):
        drop = drop_spans_with_llm(
            goal=goal,
            spans=[{"path": s["path"], "qualname": s["qualname"]} for s in tier0],
            errors=errors,
            client=client,
            cfg=cfg,
        )
        for s in tier0:
            if s["qualname"] in drop:
                actions["drop_spans"].append({"file_path": s["path"], "qualname": s["qualname"]})

    # Deduplicate
    for k in ("add_spans", "drop_spans"):
        seen = set()
        uniq = []
        for it in actions[k]:
            t = (it["file_path"], it["qualname"])
            if t in seen:
                continue
            seen.add(t)
            uniq.append(it)
        actions[k] = uniq
    return actions
