

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import ast
import difflib
import os
from pathlib import Path
import re
import textwrap

from ..filemerger import FileProcessor
from .utils import list_python_defs, prune_python_defs, slice_lines
from ..llm.prune import suggest_stubs as llm_suggest_stubs
from ..logger import get_logger, log_event, log_stage
from .retrieval import Candidate

logger = get_logger(__name__)


@dataclass
class FilePatch:


    file_path: str
    diff: str
    original: str
    updated: str


def apply_patch(patch: FilePatch) -> None:

    path = Path(patch.file_path)
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(patch.original, encoding="utf-8")
    path.write_text(patch.updated, encoding="utf-8")
    logger.info("applied patch to %s", patch.file_path)


def revert_patch(patch: FilePatch) -> None:

    path = Path(patch.file_path)
    backup = path.with_suffix(path.suffix + ".bak")
    path.write_text(patch.original, encoding="utf-8")
    if backup.exists():
        backup.unlink()
    logger.info("reverted patch on %s", patch.file_path)


def delete_defs(file_path: str, defs: list[str]) -> tuple[list[str], FilePatch | None]:

    try:
        original = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return [], None

    spans = list_python_defs(file_path)
    existing_quals = {sp.qualname for sp in spans}
    # Build reverse index: last segment -> [qualnames...]
    by_last: Dict[str, List[str]] = {}
    for sp in spans:
        last = sp.qualname.split(".")[-1]
        by_last.setdefault(last, []).append(sp.qualname)

    # Normalize requested names to qualified names present in the file
    targets: set[str] = set()
    for name in defs or []:
        if not name:
            continue
        if "." in name:
            # If a fully-qualified name is provided but not present, attempt a last-segment rescue
            if name in existing_quals:
                targets.add(name)
            else:
                tail = name.split(".")[-1]
                cands = by_last.get(tail, [])
                if len(cands) == 1:
                    logger.warning("delete_defs: resolving unknown qualname %r -> %r", name, cands[0])
                    targets.add(cands[0])
                elif len(cands) > 1:
                    tops = [q for q in cands if "." not in q]
                    if len(tops) == 1:
                        logger.warning("delete_defs: resolving ambiguous %r -> %r (top-level)", name, tops[0])
                        targets.add(tops[0])
                    else:
                        logger.warning("delete_defs: ambiguous qualname %r; candidates=%r; skipping", name, cands)
                else:
                    logger.warning("delete_defs: qualname %r not found in %s; skipping", name, file_path)
        else:
            cands = by_last.get(name, [])
            if len(cands) == 1:
                targets.add(cands[0])
            elif len(cands) > 1:
                tops = [q for q in cands if "." not in q]
                if len(tops) == 1:
                    targets.add(tops[0])
                else:
                    logger.warning("delete_defs: ambiguous short name %r in %s; candidates=%r; skipping", name, file_path, cands)
            else:
                logger.warning("delete_defs: name %r not found in %s; skipping", name, file_path)

    if not targets:
        return [], None

    updated = prune_python_defs(file_path, set(), set(targets), in_place=False)

    try:
        after = ast.parse(updated)
    except Exception:
        return [], None

    remaining = set()
    class _V(ast.NodeVisitor):
        def __init__(self, prefix=None) -> None:
            self.prefix = prefix or []

        def _qual(self, name: str) -> str:
            return ".".join(self.prefix + [name])

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
            qual = self._qual(node.name)
            remaining.add(qual)
            self.prefix.append(node.name)
            self.generic_visit(node)
            self.prefix.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
            qual = self._qual(node.name)
            remaining.add(qual)
            self.prefix.append(node.name)
            self.generic_visit(node)
            self.prefix.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    _V().visit(after)

    removed = [q for q in targets if q in existing_quals and q not in remaining]
    if not removed:
        return [], None

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(), updated.splitlines(), fromfile=file_path, tofile=file_path
        )
    )
    patch = FilePatch(file_path=file_path, diff=diff, original=original, updated=updated)
    logger.info("delete_defs removed %s from %s", removed, file_path)
    return removed, patch


def suggest_stubs(symbols: list[str]) -> dict:
    return llm_suggest_stubs(symbols, client=None, cfg=None)




@dataclass
class Plan:


    spans: List[Dict]
    full_files: List[str]
    dropped: List[str]
    file_tokens: Dict[str, int]
    order: List[Dict]
    budget_dropped: List[Dict] = field(default_factory=list)


def build_prune_plan(
    goal: str,
    index: Dict[str, Dict],
    cands,
    fp: FileProcessor,
    budget_tokens: int,
    mode: str = "safe",
    span_to_file_threshold: int = 120,
    keep_class_for_method: bool = True,
) -> Plan:

    with log_stage(logger, "build_plan", mode=mode):
        return _build_prune_plan(
            goal,
            index,
            cands,
            fp,
            budget_tokens,
            mode,
            span_to_file_threshold,
            keep_class_for_method,
        )


def _build_prune_plan(
    goal,
    index,
    cands,
    fp,
    budget_tokens,
    mode,
    span_to_file_threshold,
    keep_class_for_method,
) -> Plan:

    # Cache parsed AST information per file
    ast_cache: Dict[str, Dict] = {}

    def _parse(path: str) -> Dict:

        if path in ast_cache:
            return ast_cache[path]
        info = {"defs": {}, "refs": {}, "top": {}}
        try:
            from pathlib import Path

            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            ast_cache[path] = info
            return info

        def _collect_names(node: ast.AST) -> set[str]:
            names: set[str] = set()
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    names.add(n.id)
            return names

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: List[str] = []

            def _qual(self, name: str) -> str:
                return ".".join(self.stack + [name])

            def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
                qual = self._qual(node.name)
                info["defs"][qual] = {
                    "start": node.lineno,
                    "end": node.end_lineno or node.lineno,
                    "kind": "class",
                }
                info["refs"][qual] = _collect_names(node)
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
                qual = self._qual(node.name)
                info["defs"][qual] = {
                    "start": node.lineno,
                    "end": node.end_lineno or node.lineno,
                    "kind": "def",
                }
                info["refs"][qual] = _collect_names(node)
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Assign(self, node: ast.Assign) -> None:  # type: ignore[override]
                if self.stack:
                    return
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if len(names) == 1:
                    info["top"][names[0]] = {
                        "start": node.lineno,
                        "end": node.end_lineno or node.lineno,
                        "kind": "const",
                        "qualname": names[0],
                    }

        Visitor().visit(tree)

        # Register top-level defs for reference lookup
        for qual, d in info["defs"].items():
            if "." not in qual:
                info["top"][qual] = {
                    "start": d["start"],
                    "end": d["end"],
                    "kind": d["kind"],
                    "qualname": qual,
                }

        ast_cache[path] = info
        return info

    spans: List[Dict] = []
    full_files: List[str] = []
    file_tokens: Dict[str, int] = {}
    order: List[Dict] = []
    per_file: Dict[str, Dict] = {}
    selected_tokens: Dict[str, int] = {}

    def _role(reason: str) -> str:
        if reason.startswith("repair"):
            return "repair"
        if reason in {"neighbor", "import", "attr", "ref", "same_file"}:
            return "neighbor"
        return "seed"

    # Expand seed file candidates into their top-level definition spans
    expanded: List[Candidate] = []
    for c in cands:
        if c.kind == "file" and c.reason == "seed" and c.path.endswith(".py"):
            defs = list_python_defs(c.path)
            log_event(
                logger,
                "plan_seed_file_to_spans",
                path=c.path,
                defs=len(defs),
            )
            if defs:
                for d in defs:
                    expanded.append(
                        Candidate(
                            c.path,
                            d.kind,
                            d.qualname,
                            d.start,
                            d.end,
                            c.score,
                            "same_file",
                        )
                    )
                continue
        expanded.append(c)
    cands = expanded

    for c in cands:
        path = c.path
        if path in full_files:
            continue
        info = index.get(path, {})
        file_tok = info.get("tokens", 0)
        role = _role(c.reason)

        # File level candidate or non-Python files fall back to whole file
        if c.kind == "file" or not path.endswith(".py"):
            if path not in full_files:
                full_files.append(path)
                file_tokens[path] = file_tok
                order.append(
                    {
                        "type": "file",
                        "path": path,
                        "tokens": file_tok,
                        "score": c.score,
                        "reason": c.reason,
                        "role": role,
                    }
                )
                log_event(
                    logger,
                    "plan_file_whole",
                    path=path,
                    reason="non_python" if not path.endswith(".py") else c.reason,
                    tokens=file_tok,
                )
            continue

        data = _parse(path)
        defs = data["defs"]
        qual = c.qualname or ""

        # Resolve method to enclosing class if configured
        if c.kind == "def" and "." in qual and keep_class_for_method:
            parts = qual.split(".")
            class_qual = ""  # find closest class ancestor
            for i in range(len(parts) - 1, 0, -1):
                cand = ".".join(parts[:i])
                d = defs.get(cand)
                if d and d.get("kind") == "class":
                    class_qual = cand
                    break
            if class_qual and class_qual != qual:
                log_event(
                    logger,
                    "plan_method_to_class",
                    path=path,
                    method=qual,
                    class_qual=class_qual,
                )
                qual = class_qual

        span_def = defs.get(qual)
        if not span_def:
            ast_cache.pop(path, None)
            data = _parse(path)
            defs = data["defs"]
            span_def = defs.get(qual)
        if not span_def:
            log_event(
                logger,
                "plan_no_span_match",
                path=path,
                qualname=qual,
            )
            continue

        pf = per_file.setdefault(
            path,
            {"spans": [], "file_tok": file_tok, "score": c.score, "reason": c.reason, "role": role},
        )
        pf.setdefault("file_tok", file_tok)
        pf.setdefault("score", c.score)
        pf.setdefault("reason", c.reason)
        pf.setdefault("role", role)
        if any(s.get("qualname") == qual for s in pf["spans"]):
            continue

        start, end = span_def["start"], span_def["end"]
        segment = slice_lines(path, start, end)
        tok = fp.count_tokens(segment)
        span = {
            "path": path,
            "start": start,
            "end": end,
            "qualname": qual,
            "kind": span_def.get("kind"),
            "tokens": tok,
            "score": c.score,
            "reason": c.reason,
            "role": role,
        }
        spans.append(span)
        pf["spans"].append(span)
        order.append({"type": "span", **span})
        selected_tokens[path] = selected_tokens.get(path, 0) + tok
        log_event(
            logger,
            "plan_span_keep",
            path=path,
            qualname=qual,
            kind=span_def.get("kind"),
            tokens=tok,
            reason=c.reason,
            role=role,
        )

        # Include same-file references to top-level defs/constants
        for name in data["refs"].get(qual, set()):
            ref = data["top"].get(name)
            if not ref:
                continue
            if any(s.get("qualname") == ref["qualname"] for s in pf["spans"]):
                continue
            r_start, r_end = ref["start"], ref["end"]
            r_segment = slice_lines(path, r_start, r_end)
            r_tok = fp.count_tokens(r_segment)
            r_span = {
                "path": path,
                "start": r_start,
                "end": r_end,
                "qualname": ref["qualname"],
                "kind": ref.get("kind"),
                "tokens": r_tok,
                "score": c.score,
                "reason": "ref",
                "role": role,
            }
            spans.append(r_span)
            pf["spans"].append(r_span)
            order.append({"type": "span", **r_span})
            selected_tokens[path] = selected_tokens.get(path, 0) + r_tok
            log_event(
                logger,
                "plan_span_ref",
                path=path,
                qualname=ref["qualname"],
                tokens=r_tok,
            )

    dropped = [c.path for c in cands if c.path not in full_files and all(sp["path"] != c.path for sp in spans)]

    def _should_upsize_file(total: int, file_tok: int, threshold: int) -> bool:
        if threshold <= 0:
            return False
        if file_tok and file_tok <= threshold:
            return True
        if file_tok and file_tok <= int(threshold * 1.5):
            return True
        if total <= threshold and (not file_tok or file_tok <= int(threshold * 2)):
            return True
        return False

    upsized_files: set[str] = set()
    for path, total in selected_tokens.items():
        if path in full_files:
            continue
        file_tok = per_file.get(path, {}).get("file_tok", 0)
        if span_to_file_threshold and _should_upsize_file(total, file_tok, span_to_file_threshold):
            if path not in full_files:
                full_files.append(path)
            if file_tok:
                file_tokens[path] = file_tok
                selected_tokens[path] = file_tok
            else:
                file_tokens.setdefault(path, 0)
            pf = per_file.get(path, {})
            file_entry = {
                "type": "file",
                "path": path,
                "tokens": file_tok,
                "score": pf.get("score", 0),
                "reason": pf.get("reason", ""),
                "role": pf.get("role", "neighbor"),
            }
            for existing in order:
                if existing.get("type") == "file" and existing.get("path") == path:
                    existing.update(file_entry)
                    break
            else:
                insert_at = None
                for idx, item in enumerate(order):
                    if item.get("type") == "span" and item.get("path") == path:
                        insert_at = idx
                        break
                if insert_at is None:
                    order.append(file_entry)
                else:
                    order.insert(insert_at, file_entry)
            log_event(
                logger,
                "plan_span_upsized_file",
                path=path,
                tokens=total,
                threshold=span_to_file_threshold,
            )
            upsized_files.add(path)

    if upsized_files:
        spans = [sp for sp in spans if sp.get("path") not in upsized_files]
        new_order: List[Dict] = []
        for item in order:
            if isinstance(item, dict) and item.get("type") == "span" and item.get("path") in upsized_files:
                continue
            new_order.append(item)
        order = new_order
        for up_path in upsized_files:
            pf = per_file.get(up_path)
            if isinstance(pf, dict):
                pf["spans"] = []

    plan = Plan(
        spans=spans,
        full_files=full_files,
        dropped=dropped,
        file_tokens=file_tokens,
        order=order,
    )
    log_event(
        logger,
        "plan_decisions",
        kept_files=len(full_files),
        kept_spans=len(spans),
        selected_tokens=sum(selected_tokens.values()),
    )
    return plan


def write_pruned_merge(
    plan: Plan,
    root: str,
    out_path: str,
    fp: FileProcessor,
    *,
    include_tree: bool = True,
    enumerate_lines: bool = False,
    remove_empty: bool = False,
    budget_tokens: int = 0,
    core_budget_tokens: int = 0,
    mode: str = "safe",
    log_entries: List[str] | None = None,
    single_file_strict: bool = False,
    single_file_max_tokens: int = 0,
) -> Tuple[str, str | None, Dict[str, int], Dict[str, int]]:

    import tempfile
    import shutil
    import os
    import contextlib

    requested_budget_tokens = max(0, int(budget_tokens or 0))

    def _write_plan_log(target: str) -> None:
        if not log_entries:
            return
        log_path = target + ".log"
        try:
            Path(log_path).write_text("\n".join(log_entries) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("failed to write prune log for %s", log_path)

    # 1) Materialize pruned tree (now emits class skeletons where needed)
    temp_dir = tempfile.mkdtemp(prefix="codemerge_pruned_")
    START_LABEL = "======= START OF FILE: {file_display} ======="
    END_LABEL = "======= END OF FILE: {file_display} ======="
    TREE_START = "======= FILE TREE ======="
    TREE_END = "======= END OF FILE TREE ======="
    write_pruned_tree(plan, root, temp_dir)

    # 2) Collect text files and map them back to original plan paths
    files: list[str] = []
    for r, _, names in os.walk(temp_dir):
        for nm in names:
            p = os.path.join(r, nm)
            if fp.is_binary_file(p):
                continue
            files.append(p)

    # Preserve plan intention: derive ordering/priorities from plan.order
    order_meta: Dict[str, Dict[str, object]] = {}
    order_meta_rel: Dict[str, Dict[str, object]] = {}
    root_resolved = Path(root).resolve()
    for idx, item in enumerate(plan.order):
        path = item.get("path") if isinstance(item, dict) else None
        if not path:
            continue
        try:
            path_key = str(Path(path).resolve())
        except Exception:
            path_key = str(path)
        meta = order_meta.setdefault(path_key, {"order": idx, "reason": None, "role": None})
        meta["order"] = min(meta.get("order", idx), idx)
        reason = item.get("reason")
        role = item.get("role")
        if reason and not meta.get("reason"):
            meta["reason"] = reason
        if role and not meta.get("role"):
            meta["role"] = role
        try:
            rel_key = os.path.relpath(path_key, str(root_resolved))
        except Exception:
            rel_key = ""
        if rel_key:
            rel_meta = order_meta_rel.setdefault(rel_key, {"order": idx, "reason": None, "role": None})
            rel_meta["order"] = min(rel_meta.get("order", idx), idx)
            if reason and not rel_meta.get("reason"):
                rel_meta["reason"] = reason
            if role and not rel_meta.get("role"):
                rel_meta["role"] = role

    file_infos: List[Dict[str, object]] = []
    total_tokens = 0
    for p in files:
        rel = os.path.relpath(p, temp_dir)
        try:
            orig = str((root_resolved / rel).resolve())
        except Exception:
            orig = os.path.normpath(os.path.join(str(root_resolved), rel))
        tokens = fp.file_token_count(p) or 0
        meta = order_meta.get(orig) or order_meta_rel.get(rel, {})
        file_infos.append(
            {
                "temp": p,
                "orig": orig,
                "rel": rel,
                "tokens": tokens,
                "order": meta.get("order", len(plan.order) + 100),
                "reason": meta.get("reason"),
                "role": meta.get("role"),
            }
        )
        total_tokens += tokens

    role_priority = {"seed": 0, "repair": 1, "neighbor": 2}
    file_infos.sort(
        key=lambda info: (
            role_priority.get(info.get("role"), 3),
            info.get("order", len(plan.order) + 100),
            info.get("rel", ""),
        )
    )
    files = [info["temp"] for info in file_infos]

    def _why_map(infos: List[Dict[str, object]]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for info in infos:
            reason = info.get("reason") or info.get("role")
            if reason:
                mapping[str(info["temp"])] = str(reason)
        return mapping

    why_by_file = _why_map(file_infos)
    core_file_set = {info["temp"] for info in file_infos if info.get("role") in {"seed", "repair"}}
    core_total_tokens = sum((info.get("tokens", 0) or 0) for info in file_infos if info["temp"] in core_file_set)
    compact_dropped: List[Dict[str, object]] = []
    token_by_temp = {info["temp"]: info.get("tokens", 0) or 0 for info in file_infos}
    base_stats = {
        "kept_defs": len(plan.spans),
        "kept_files": len(file_infos),
        "tokens": sum(token_by_temp.values()),
    }

    def _count_tokens_safe(text: str) -> int:
        try:
            return int(fp.count_tokens(text))
        except Exception:
            return max(len(text) // 4, 1)

    def _merge_tokens(file_list: List[str]) -> int:
        total, _ = fp.merge_group_token_details(
            temp_dir,
            file_list,
            include_file_tree=include_tree,
            custom_start_label=START_LABEL,
            custom_end_label=END_LABEL,
            file_tree_start_label=TREE_START,
            file_tree_end_label=TREE_END,
            prompt_template=None,
            remove_empty=remove_empty,
            enumerate_lines=enumerate_lines,
            why_by_file=why_by_file,
        )
        return total

    note_text = "NOTE: Repository/folder summaries are in chunk 1.\n\n"
    note_tokens = _count_tokens_safe(note_text)
    tree_tokens = 0
    if include_tree:
        try:
            tree_text = fp.generate_file_tree(temp_dir, files)
        except Exception:
            tree_text = ""
        tree_block = f"{TREE_START}\n{tree_text}\n{TREE_END}\n\n"
        tree_tokens = _count_tokens_safe(tree_block)

    def _split_groups(limit: int) -> List[List[str]]:
        if limit <= 0 or not files:
            return [list(files)] if files else []

        groups: List[List[str]] = []
        current: List[str] = []
        for path in files:
            group_index = len(groups) + 1
            candidate = current + [path]
            cand_total, _ = fp.merge_group_token_details(
                temp_dir,
                candidate,
                include_file_tree=False,
                custom_start_label=START_LABEL,
                custom_end_label=END_LABEL,
                file_tree_start_label=TREE_START,
                file_tree_end_label=TREE_END,
                prompt_template=None,
                remove_empty=remove_empty,
                enumerate_lines=enumerate_lines,
                markdown_fences=False,
                markdown_toc=False,
                content_mode="code_only",
                summaries_by_file=None,
                files_with_summary_errors=None,
                why_by_file=why_by_file,
            )
            if group_index == 1:
                cand_total += tree_tokens
            else:
                cand_total += note_tokens
            if current and cand_total > limit:
                groups.append(current)
                current = [path]
            else:
                current = candidate
        if current:
            groups.append(current)
        return groups

    core_required_paths = [info["temp"] for info in file_infos if info["temp"] in core_file_set]
    try:
        core_required_budget = _merge_tokens(core_required_paths) if core_required_paths else 0
    except Exception:
        core_required_budget = sum((info.get("tokens", 0) or 0) for info in file_infos if info["temp"] in core_file_set)

    if mode == "compact":
        compact_limit = requested_budget_tokens
        if compact_limit > 0:
            kept_infos: List[Dict[str, object]] = []
            remaining = compact_limit
            for info in file_infos:
                role = info.get("role")
                tokens = info.get("tokens", 0) or 0
                if role in {"seed", "repair"}:
                    kept_infos.append(info)
                    remaining = max(0, remaining - tokens)
                    continue
                if remaining and tokens <= remaining:
                    kept_infos.append(info)
                    remaining -= tokens
                else:
                    compact_dropped.append(info)
            file_infos = kept_infos
        files = [info["temp"] for info in file_infos]
        why_by_file = _why_map(file_infos)
        core_file_set = {info["temp"] for info in file_infos if info.get("role") in {"seed", "repair"}}
        core_total_tokens = sum((info.get("tokens", 0) or 0) for info in file_infos if info["temp"] in core_file_set)
        total_tokens = sum((info.get("tokens", 0) or 0) for info in file_infos)
        token_by_temp = {info["temp"]: info.get("tokens", 0) or 0 for info in file_infos}
        base_stats = {
            "kept_defs": len(plan.spans),
            "kept_files": len(file_infos),
            "tokens": sum(token_by_temp.values()),
        }
        budget_tokens = 0  # compact mode emits a single pack without overflow

    # 4) Strict single-file check (optional)
    extra_stats = {
        "kept_defs": 0,
        "kept_files": 0,
        "tokens": 0,
        "compact_dropped_files": len(compact_dropped),
        "compact_dropped_tokens": sum((info.get("tokens", 0) or 0) for info in compact_dropped),
    }
    if single_file_strict and single_file_max_tokens > 0:
        if total_tokens > single_file_max_tokens:
            # write chunks as overflow and report loud failure by setting overflow path
            base, ext = os.path.splitext(out_path)
            out_folder = base + "_chunks" if ext else (out_path.rstrip("/") + "_chunks")
            uniq = out_folder
            i = 2
            while os.path.exists(uniq):
                uniq = f"{out_folder}_{i}"
                i += 1
            fp.merge_files_token_split(
                directory=temp_dir,
                files_to_merge=files,
                output_folder=uniq,
                token_limit=single_file_max_tokens,
                progress_callback=None,
                custom_start_label="======= START OF FILE: {file_display} =======",
                custom_end_label="======= END OF FILE: {file_display} =======",
                file_tree_start_label="======= FILE TREE =======",
                file_tree_end_label="======= END OF FILE TREE =======",
                include_file_tree=include_tree,
                create_json_manifest=False,
                summaries_by_file=None,
                content_mode="code_only",
                files_with_summary_errors=None,
                prompt_template=None,
                verb="Merging",
                remove_empty=remove_empty,
                enumerate_lines=enumerate_lines,
                why_by_file=why_by_file,
            )
            with contextlib.suppress(Exception):
                shutil.rmtree(temp_dir, ignore_errors=True)
            main_stats = base_stats
            return uniq, uniq, main_stats, extra_stats

    if budget_tokens and budget_tokens > 0:
        base, ext = os.path.splitext(out_path)
        out_folder = base + "_chunks" if ext else (out_path.rstrip("/") + "_chunks")
        uniq = out_folder
        i = 2
        while os.path.exists(uniq):
            uniq = f"{out_folder}_{i}"
            i += 1
        effective_limit = budget_tokens
        if core_budget_tokens:
            effective_limit = max(effective_limit, core_budget_tokens)
        if core_file_set:
            effective_limit = max(effective_limit, core_total_tokens)
        if core_required_budget:
            effective_limit = max(effective_limit, core_required_budget)
        if core_file_set:
            try:
                core_group_tokens, _ = fp.merge_group_token_details(
                    temp_dir,
                    core_required_paths,
                    include_file_tree=False,
                    custom_start_label=START_LABEL,
                    custom_end_label=END_LABEL,
                    file_tree_start_label=TREE_START,
                    file_tree_end_label=TREE_END,
                    prompt_template=None,
                    remove_empty=remove_empty,
                    enumerate_lines=enumerate_lines,
                    markdown_fences=False,
                    markdown_toc=False,
                    content_mode="code_only",
                    summaries_by_file=None,
                    files_with_summary_errors=None,
                    why_by_file=why_by_file,
                )
                core_group_budget = core_group_tokens + tree_tokens
                effective_limit = max(effective_limit, core_group_budget)
            except Exception:
                pass

        groups = _split_groups(effective_limit)
        if len(groups) <= 1:
            budget_tokens = 0
        else:
            fp.merge_files_token_split(
                directory=temp_dir,
                files_to_merge=files,
                output_folder=uniq,
                token_limit=effective_limit,
                progress_callback=None,
                custom_start_label=START_LABEL,
                custom_end_label=END_LABEL,
                file_tree_start_label=TREE_START,
                file_tree_end_label=TREE_END,
                include_file_tree=include_tree,
                create_json_manifest=False,
                summaries_by_file=None,
                content_mode="code_only",
                files_with_summary_errors=None,
                prompt_template=None,
                verb="Merging",
                remove_empty=remove_empty,
                enumerate_lines=enumerate_lines,
                why_by_file=why_by_file,
            )

            chunk_output_paths = [
                os.path.join(uniq, f"{idx}.txt")
                for idx in range(1, len(groups) + 1)
            ]

            chunk_existing = [p for p in chunk_output_paths if os.path.exists(p)]
            if len(chunk_existing) != len(groups):
                chunk_existing = sorted(
                    (
                        str(p)
                        for p in Path(uniq).glob("*.txt")
                        if p.is_file() and p.name != "multi-merge-overview.txt"
                    ),
                    key=lambda p: (
                        0,
                        int(Path(p).stem),
                    )
                    if Path(p).stem.isdigit()
                    else (1, Path(p).name),
                )
            if chunk_existing:
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                shutil.copyfile(chunk_existing[0], out_path)
            else:
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                Path(out_path).write_text("", encoding="utf-8")

            overflow_path = None
            if len(chunk_existing) > 1:
                overflow_path = out_path + ".overflow.txt"
                with open(overflow_path, "w", encoding="utf-8") as fh_over:
                    for idx, extra in enumerate(chunk_existing[1:]):
                        text = Path(extra).read_text(encoding="utf-8")
                        if idx and not text.startswith("\n"):
                            fh_over.write("\n")
                        fh_over.write(text)

            main_tokens = sum(token_by_temp.get(p, fp.file_token_count(p) or 0) for p in groups[0])
            main_stats = {
                "kept_defs": len(plan.spans),
                "kept_files": len(groups[0]),
                "tokens": main_tokens,
            }
            overflow_tokens = sum(
                token_by_temp.get(p, fp.file_token_count(p) or 0)
                for grp in groups[1:]
                for p in grp
            )
            extra_stats.update(
                {
                    "kept_files": sum(len(grp) for grp in groups[1:]),
                    "tokens": overflow_tokens,
                }
            )

            with contextlib.suppress(Exception):
                shutil.rmtree(temp_dir, ignore_errors=True)
            _write_plan_log(out_path)
            return out_path, overflow_path, main_stats, extra_stats

    # Single merged file
    fp.merge_files(
        directory=temp_dir,
        files_to_merge=files,
        output_file=out_path,
        include_file_tree=include_tree,
        enumerate_lines=enumerate_lines,
        remove_empty=remove_empty,
        why_by_file=why_by_file,
    )
    with contextlib.suppress(Exception):
        shutil.rmtree(temp_dir, ignore_errors=True)
    stats_main = {
        "kept_defs": len(plan.spans),
        "kept_files": len(file_infos),
        "tokens": sum(token_by_temp.get(p, fp.file_token_count(p) or 0) for p in files),
    }
    _write_plan_log(out_path)
    return out_path, None, stats_main, extra_stats


def write_pruned_tree(plan: Plan, root: str, out_dir: str, _fp: FileProcessor | None = None) -> None:

    os.makedirs(out_dir, exist_ok=True)
    root_resolved = Path(root).resolve()

    # 1) Mirror all full file selections
    for p in plan.full_files:
        abs_path = Path(p).resolve()
        rel = os.path.relpath(abs_path, root_resolved)
        dest = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(abs_path, "rb") as fh_src, open(dest, "wb") as fh_out:
            fh_out.write(fh_src.read())

    # 2) Group spans per file
    per_file: Dict[str, List[Dict]] = {}
    for sp in plan.spans:
        if sp["path"] in plan.full_files:
            continue
        per_file.setdefault(sp["path"], []).append(sp)

    # Helper: compose nested class skeletons from method spans
    def _compose_class_skeleton(path: str, methods: List[Dict]) -> str:

        tree = {"__methods__": [], "__classes__": {}}
        # build nested dict structure
        for sp in methods:
            parts = sp["qualname"].split(".")
            cls_chain = parts[:-1]
            cur = tree
            for cls in cls_chain:
                cur = cur["__classes__"].setdefault(cls, {"__methods__": [], "__classes__": {}})
            cur["__methods" if False else "__methods__"].append(sp)

        def indent(text: str, n: int) -> str:
            pad = " " * n
            return "\n".join(pad + ln if ln.strip() else ln for ln in text.splitlines())

        def emit(node, name: Optional[str], level: int) -> str:
            out: List[str] = []
            if name is not None:
                out.append((" " * level) + f"class {name}:\n")
                level += 4
            # methods
            for m in sorted(node["__methods__"], key=lambda s: (s["start"], s["end"])):
                seg = slice_lines(path, m["start"], m["end"]) or ""
                # Dedent original to remove prior nesting, then re-indent under class
                seg = textwrap.dedent(seg.rstrip("\n"))
                out.append(indent(seg, level) + "\n")
            # nested classes
            for cname in sorted(node["__classes__"].keys()):
                out.append(emit(node["__classes__"][cname], cname, level))
            if name is not None and not node["__methods__"] and not node["__classes__"]:
                out.append((" " * level) + "pass\n")
            return "".join(out)

        return emit(tree, None, 0)

    def _extract_top_imports_text(path: str) -> str:

        try:
            src = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            return ""
        lines: list[str] = []
        for node in getattr(tree, "body", []) or []:
            try:
                if isinstance(node, ast.Import):
                    parts = []
                    for a in node.names:
                        if a.asname:
                            parts.append(f"{a.name} as {a.asname}")
                        else:
                            parts.append(a.name)
                    lines.append("import " + ", ".join(parts))
                elif isinstance(node, ast.ImportFrom):
                    level_dots = "." * (node.level or 0)
                    module = node.module or ""
                    head = f"from {level_dots}{module} import ".rstrip()
                    parts = []
                    for a in node.names:
                        if a.asname:
                            parts.append(f"{a.name} as {a.asname}")
                        else:
                            parts.append(a.name)
                    lines.append(head + ", ".join(parts))
            except Exception:
                # Be permissive; skip malformed entries
                continue
        if not lines:
            return ""
        return "\n".join(lines) + "\n\n"

    # 3) Write per-file content, handling top-level spans and method-only selections
    for path, spans in per_file.items():
        abs_path = Path(path).resolve()
        rel = os.path.relpath(abs_path, root_resolved)
        dest = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        spans.sort(key=lambda s: (s["start"], s["end"]))
        # split into categories
        full_class_quals = {s["qualname"] for s in spans if s.get("kind") == "class"}

        # Collect class qualnames present in source (for accurate method detection)
        def _class_quals_from_ast(p: str) -> set[str]:
            quals: set[str] = set()
            try:
                src = Path(p).read_text(encoding="utf-8")
                tree = ast.parse(src)
            except Exception:
                return quals

            class V(ast.NodeVisitor):
                def __init__(self):
                    self.stack: list[str] = []

                def _qual(self, n: str) -> str:
                    return ".".join(self.stack + [n])

                def visit_ClassDef(self, n: ast.ClassDef):
                    q = self._qual(n.name)
                    quals.add(q)
                    self.stack.append(n.name)
                    self.generic_visit(n)
                    self.stack.pop()

            V().visit(tree)
            return quals

        class_quals_in_src = _class_quals_from_ast(path)

        def _any_prefix_in(target: str, pool: set[str]) -> bool:
            parts = target.split(".")
            # Check any class prefix (one or more segments, excluding the full def)
            for i in range(1, len(parts)):
                if ".".join(parts[:i]) in pool:
                    return True
            return False

        # True methods: defs whose qualname chain contains a class from source
        candidate_methods = [s for s in spans if s.get("kind") == "def" and "." in s.get("qualname", "") and _any_prefix_in(s["qualname"], class_quals_in_src)]
        # Exclude methods whose enclosing class (any prefix) is already fully kept as a class span
        method_spans = [s for s in candidate_methods if not _any_prefix_in(s["qualname"], full_class_quals)]
        top_level_spans = [s for s in spans if s not in method_spans]

        with open(dest, "w", encoding="utf-8", newline="") as fh:
            # If emitting any partial content (spans), include top-level imports first for runtime correctness
            if method_spans or top_level_spans:
                imp_text = _extract_top_imports_text(path)
                if imp_text:
                    fh.write(imp_text)
            # write non-method spans verbatim
            last_seg = ""
            for sp in top_level_spans:
                seg = slice_lines(abs_path, sp["start"], sp["end"])
                fh.write(seg)
                last_seg = seg
            # emit class skeletons for method-only classes
            if method_spans:
                fh.write(_compose_class_skeleton(str(abs_path), method_spans))
            if (last_seg and not last_seg.endswith("\n")):
                fh.write("\n")


# Regex for locating environment key access patterns in Python source
_ENV_KEY_RX = re.compile(
    r'(?:os\.environ\[\s*[\'\"](?P<k1>[A-Za-z_][A-Za-z0-9_]*)[\'\"]\s*\])|'
    r'(?:os\.getenv\(\s*[\'\"](?P<k2>[A-Za-z_][A-Za-z0-9_]*)[\'\"]\s*(?:,|\)))'
)


def _collect_env_keys(items: List[Dict], root: str) -> set[str]:

    keys: set[str] = set()
    for it in items:
        path = it.get("path", "")
        if not path:
            continue
        try:
            if it.get("type") == "file":
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
            else:
                text = slice_lines(path, it.get("start", 1), it.get("end", 1))
        except Exception:
            continue
        for m in _ENV_KEY_RX.finditer(text or ""):
            k = m.group("k1") or m.group("k2")
            if k:
                keys.add(k)
    return keys


def _append_env_sample(out_path: str, items: List[Dict], root: str) -> None:

    keys = sorted(_collect_env_keys(items, root))
    if not keys:
        return
    lines = ["======= ENV SAMPLE ======="]
    for k in keys:
        # Values blank by design
        lines.append(f"{k}=")
    lines.append("======= END OF ENV SAMPLE =======")
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(lines) + "\n")
    except Exception:
        logger.debug("could not append env sample to %s", out_path)
