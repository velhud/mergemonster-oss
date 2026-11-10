from __future__ import annotations
from typing import Dict, List, Optional, Iterable
from pathlib import Path
import tempfile
import shutil
import subprocess
import re
import ast
import builtins

from .selector import select as select_spans
from .closure import build_minimal, Span as _SpanObj
from .harness import synthesize as synth_harness
from .runner import run_with_coverage
from .critic import refine as critic_refine
from .pruner import write_pruned_tree, Plan  # reuse Plan for final packaging shape

UNDEFINED_NAME_RX = re.compile(r"^(?P<file>.*?):\d+:\s*undefined name '([^']+)'", re.IGNORECASE)
MISSING_IMPORT_RX = re.compile(r"^(?P<file>.*?):\d+:\s*Unable to import '([^']+)'", re.IGNORECASE)
DYNAMIC_IMPORT_RX = re.compile(r"__import__\(\s*['\"]([^'\"]+)['\"]\s*\)")
IMPORTLIB_RX = re.compile(r"importlib\.import_module\(\s*['\"]([^'\"]+)['\"]")


def _span_dict(sp: _SpanObj) -> dict:
    role = getattr(sp, "role", "seed")
    return {
        "path": sp.path,
        "start": sp.start,
        "end": sp.end,
        "qualname": sp.qualname,
        "kind": sp.kind,
        "reason": sp.why,
        "role": role,
    }


def _plan_from_tiers(root: str, tier0: List[dict], tier1: List[dict]) -> Plan:
    """
    Convert closure tiers into a pruner.Plan:
    - tier0 spans become span entries
    - tier1 file-level entries ('<FILE>') become full files
    """
    spans: List[Dict] = []
    full_files: List[str] = []
    order: List[Dict] = []
    for sp in tier0:
        if sp.get("kind") == "file" or sp.get("qualname") == "<FILE>":
            if sp["path"] not in full_files:
                full_files.append(sp["path"])
        else:
            span_entry = {
                "path": sp["path"],
                "start": sp["start"],
                "end": sp["end"],
                "qualname": sp["qualname"],
                "kind": sp.get("kind"),
                "tokens": 0,
                "score": 0.0,
                "reason": sp.get("reason", "closure"),
                "role": sp.get("role", "seed"),
            }
            spans.append(span_entry)
            order.append({"type": "span", **span_entry})
    for sp in tier1:
        if sp.get("kind") == "file" or sp.get("qualname") == "<FILE>":
            if sp["path"] not in full_files:
                full_files.append(sp["path"])
            file_entry = {
                "type": "file",
                "path": sp["path"],
                "tokens": 0,
                "score": 0.0,
                "reason": sp.get("reason", "closure"),
                "role": sp.get("role", "neighbor"),
            }
            order.append(file_entry)
    return Plan(spans=spans, full_files=full_files, dropped=[], file_tokens={}, order=order)


def _extract_kept_from_plan(plan) -> List[dict]:
    """Turn an existing plan into list of picks [{file_path, qualname}]."""
    picks: List[dict] = []
    reason_meta: Dict[tuple[str, str], Dict[str, str]] = {}
    order_items = getattr(plan, "order", None)
    if order_items is None and isinstance(plan, dict):
        order_items = plan.get("order", [])
    for item in order_items or []:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not path:
            continue
        reason = item.get("reason")
        role = item.get("role")
        if item.get("type") == "file":
            key = (path, "<FILE>")
        else:
            key = (path, item.get("qualname") or "")
        reason_meta[key] = {"reason": reason, "role": role}
    spans = getattr(plan, "spans", None)
    if spans is None and isinstance(plan, dict):
        spans = plan.get("spans", [])
    if not spans:
        spans = []
    seen: set[tuple[str | None, str | None]] = set()
    for sp in spans:
        if not isinstance(sp, dict):
            continue
        qual = sp.get("qualname")
        if qual:
            file_path = sp.get("path")
            meta = reason_meta.get((file_path, qual), {})
            picks.append(
                {
                    "file_path": file_path,
                    "qualname": qual,
                    "reason": meta.get("reason") or sp.get("reason"),
                    "role": meta.get("role") or sp.get("role"),
                }
            )
            seen.add((file_path, qual))
    # whole files do not have qualnames; they will be pulled in via closure as Tier-1 when needed
    full_files = getattr(plan, "full_files", None)
    if full_files is None and isinstance(plan, dict):
        full_files = plan.get("full_files", [])
    for path in full_files or []:
        if not path:
            continue
        key = (path, "<FILE>")
        if key in seen:
            continue
        meta = reason_meta.get(key, {})
        picks.append(
            {
                "file_path": path,
                "qualname": "<FILE>",
                "reason": meta.get("reason"),
                "role": meta.get("role"),
            }
        )
        seen.add(key)
    return picks


def validate_and_repair(
    plan,
    root: str,
    index: Dict[str, Dict],
    fp,  # api compatibility
    *,
    max_passes: int = 3,
    allowed_paths: Optional[Iterable[str]] = None,
    repair_prefer_spans: bool = True,
    observer_rounds: int = 0,
    prune_goal_llm_span_select: bool = True,
    prune_keep_class_for_method: bool = False,
    groq_strict_mode: bool = True,
    **kwargs,
):
    """
    Orchestrator loop:
      select -> closure -> materialize -> harness -> run -> critic -> refine, repeat.
    Returns (updated_plan, logs).
    """
    logs: List[str] = []

    def _emit_log(message: str, role: str | None = None) -> None:
        if not message:
            return
        if role:
            logs.append(f"{message}\t{role}")
        else:
            logs.append(message)

    root_path = Path(root).absolute()
    root_resolved = root_path.resolve()
    allowed_set = {str(Path(p).resolve()) for p in allowed_paths} if allowed_paths else None

    # Record initial reasons from the plan so downstream logs include seed context
    order_items = getattr(plan, "order", None)
    if order_items is None:
        order_items = plan.get("order", []) if isinstance(plan, dict) else []
    reasons: list[str] = []
    seen_plan_entries: set[tuple[str | None, str | None, str | None]] = set()
    for item in order_items:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        reason = item.get("reason")
        role_hint = item.get("role")
        item_type = item.get("type")
        if not role_hint:
            role_hint = "neighbor" if item_type == "file" else "seed"
        str_reason = str(reason) if reason is not None else None
        if str_reason:
            reasons.append(str_reason)
        key = (path, str_reason, role_hint)
        if key in seen_plan_entries:
            continue
        seen_plan_entries.add(key)
        parts: list[str] = []
        if path:
            parts.append(Path(path).name)
        qual = item.get("qualname")
        if qual and qual != "<FILE>":
            parts.append(str(qual))
        if str_reason:
            parts.append(f"reason={str_reason}")
        message = "plan:"
        if parts:
            message += " " + ", ".join(parts)
        _emit_log(message, role_hint)

    if not reasons:
        span_items = getattr(plan, "spans", None)
        if span_items is None:
            span_items = plan.get("spans", []) if isinstance(plan, dict) else []
        for sp in span_items:
            if not isinstance(sp, dict):
                continue
            path = sp.get("path")
            reason = sp.get("reason")
            role_hint = sp.get("role") or "seed"
            str_reason = str(reason) if reason is not None else None
            if str_reason:
                reasons.append(str_reason)
            key = (path, str_reason, role_hint)
            if key in seen_plan_entries:
                continue
            seen_plan_entries.add(key)
            parts: list[str] = []
            if path:
                parts.append(Path(path).name)
            qual = sp.get("qualname")
            if qual and qual != "<FILE>":
                parts.append(str(qual))
            if str_reason:
                parts.append(f"reason={str_reason}")
            message = "plan:"
            if parts:
                message += " " + ", ".join(parts)
            _emit_log(message, role_hint)

    if reasons:
        uniq = sorted({r for r in reasons if r})
        _emit_log("plan: reasons=" + ", ".join(uniq))

    # 0) Initial picks
    picks = _extract_kept_from_plan(plan)
    if prune_goal_llm_span_select:
        try:
            picks_llm = select_spans(kwargs.get("goal", ""), index, client=None, cfg=kwargs)
            if picks_llm:
                picks = picks_llm
                _emit_log(f"selector: {len(picks)} spans from LLM.")
        except Exception as e:
            _emit_log(f"selector-error: {e}")

    # 1) Build minimal closure
    tier0_objs, tier1_objs = build_minimal(
        root, picks, index, keep_class_for_method=prune_keep_class_for_method, allowed_paths=allowed_paths
    )
    tier0: List[dict] = [_span_dict(s) for s in tier0_objs]
    tier1: List[dict] = [_span_dict(s) for s in tier1_objs]
    _emit_log(f"closure: tier0={len(tier0)} tier1={len(tier1)}")

    # 2..N) Observer loop
    rounds = max(0, int(observer_rounds or 0))
    for r in range(1, rounds + 1):
        # Materialize pruned tree to a temp folder
        tmp = Path(tempfile.mkdtemp(prefix=f"codemerge_observe_{r}_"))
        try:
            plan_r = _plan_from_tiers(root, tier0, tier1)
            write_pruned_tree(plan_r, root, str(tmp))
            # 2) Synthesize harness and run
            scripts = []
            try:
                scripts = synth_harness(kwargs.get("goal", ""), root, tier0, client=None, cfg=kwargs)
            except Exception as e:
                _emit_log(f"harness-error: {e}")
            if not scripts:
                scripts = ["import importlib; pass"]
            trace = run_with_coverage(str(tmp), scripts, timeout_sec=int(kwargs.get("runner_timeout_sec", 20)))
            _emit_log(
                f"runner: executed files={len(trace.get('executed', {}))} errors={len(trace.get('errors', []))} missing={len(trace.get('missing_modules', []))}"
            )

            # 3) Critic
            edits = critic_refine(kwargs.get("goal", ""), tier0, tier1, trace, index, client=None, cfg=kwargs)
            add_spans = edits.get("add_spans", [])
            drop_spans = edits.get("drop_spans", [])

            # Apply adds
            for it in add_spans:
                p = it["file_path"]
                q = it["qualname"]
                if not any(s["path"] == p and s["qualname"] == q for s in tier0):
                    # locate span from index
                    for d in index.get(p, {}).get("defs", []) or []:
                        if d.get("qualname") == q:
                            tier0.append({
                                "path": p,
                                "start": d.get("start"),
                                "end": d.get("end"),
                                "qualname": q,
                                "kind": d.get("kind"),
                                "reason": "coverage",
                                "role": "seed",
                            })
                            break

            # Apply drops
            if drop_spans:
                keep = {(s["path"], s["qualname"]) for s in tier0}
                rm = {(d["file_path"], d["qualname"]) for d in drop_spans}
                if rm & keep:
                    tier0 = [s for s in tier0 if (s["path"], s["qualname"]) not in rm]

            # Rebuild closure after edits (minimal refresh)
            # Convert tier0 to picks:
            picks2 = []
            seen_pick: set[tuple[str, str]] = set()
            for s in tier0:
                path = s.get("path")
                qual = s.get("qualname")
                if not path or not qual:
                    continue
                key = (path, qual)
                if qual == "<FILE>" or key in seen_pick:
                    continue
                picks2.append({"file_path": path, "qualname": qual})
                seen_pick.add(key)
            for s in tier1:
                path = s.get("path")
                if not path:
                    continue
                key = (path, "<FILE>")
                if key in seen_pick:
                    continue
                if s.get("kind") == "file" or s.get("qualname") == "<FILE>":
                    picks2.append(
                        {
                            "file_path": path,
                            "qualname": "<FILE>",
                            "reason": s.get("reason"),
                            "role": s.get("role"),
                        }
                    )
                    seen_pick.add(key)
            tier0_objs, tier1_objs = build_minimal(
                root, picks2, index, keep_class_for_method=prune_keep_class_for_method, allowed_paths=allowed_paths
            )
            tier0 = [
                {
                    "path": s.path,
                    "start": s.start,
                    "end": s.end,
                    "qualname": s.qualname,
                    "kind": s.kind,
                    "reason": s.why,
                    "role": "seed",
                }
                for s in tier0_objs
            ]
            tier1 = [
                {
                    "path": s.path,
                    "start": s.start,
                    "end": s.end,
                    "qualname": s.qualname,
                    "kind": s.kind,
                    "reason": s.why,
                    "role": "neighbor",
                }
                for s in tier1_objs
            ]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Static checker assisted repairs (pyflakes/pyright)
    static_messages = _run_static_check(root, allowed_paths=allowed_set)
    if static_messages:
        _emit_log(f"static-check: {len(static_messages)} findings")

    module_map: Dict[str, str] = {}
    for path in index.keys():
        try:
            rel = Path(path).resolve().relative_to(root_resolved)
        except Exception:
            continue
        if rel.suffix != ".py":
            continue
        module_name = ".".join(rel.with_suffix("").parts)
        if module_name:
            module_map.setdefault(module_name, path)
        if rel.name == "__init__.py":
            package_name = ".".join(rel.parent.parts)
            if package_name:
                module_map.setdefault(package_name, path)

    name_map: Dict[str, List[Dict[str, object]]] = {}
    for path, info in index.items():
        for d in info.get("defs", []) or []:
            start = d.get("start")
            end = d.get("end")
            if start is None or end is None:
                continue
            entry = {
                "path": path,
                "start": start,
                "end": end,
                "qualname": d.get("qualname"),
                "kind": d.get("kind"),
            }
            name = d.get("name")
            if name:
                name_map.setdefault(name, []).append(entry)
            qual = d.get("qualname")
            if qual:
                tail = qual.split(".")[-1]
                if tail:
                    name_map.setdefault(tail, []).append(entry)

    existing_spans = {(s["path"], s.get("qualname")) for s in tier0}
    existing_files = {s["path"] for s in tier1 if s.get("qualname") == "<FILE>" or s.get("kind") == "file"}

    builtin_names = set(dir(builtins)) | {"True", "False", "None"}
    analysis_cache: Dict[str, Dict[str, object]] = {}
    module_defs_cache: Dict[str, List[Dict[str, object]]] = {}
    expanded_star_modules: set[str] = set()

    def _analysis_for(path: str) -> Dict[str, object]:
        cached = analysis_cache.get(path)
        if cached is not None:
            return cached
        locals_by_qual: Dict[str, set[str]] = {}
        module_imports: set[str] = set()
        module_globals: set[str] = set()
        star_imports: set[str] = set()
        refs_by_qual: Dict[str, set[str]] = {}
        try:
            src = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            data = {
                "locals": locals_by_qual,
                "imports": module_imports,
                "globals": module_globals,
                "refs": refs_by_qual,
                "star_imports": star_imports,
            }
            analysis_cache[path] = data
            return data

        def _target_names(target: ast.AST) -> set[str]:
            names: set[str] = set()
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for elt in target.elts:
                    names.update(_target_names(elt))
            return names

        class _LocalCollector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.names: set[str] = set()

            def visit_Name(self, node: ast.Name) -> None:  # type: ignore[override]
                if isinstance(node.ctx, ast.Store):
                    self.names.add(node.id)

            def visit_Import(self, node: ast.Import) -> None:  # type: ignore[override]
                for alias in node.names:
                    self.names.add(alias.asname or alias.name.split(".")[0])

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # type: ignore[override]
                for alias in node.names:
                    self.names.add(alias.asname or alias.name)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
                return

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
                return

        def _load_names(node: ast.AST) -> set[str]:
            names: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                    names.add(child.id)
            return names

        class _Analyzer(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def visit_Import(self, node: ast.Import) -> None:  # type: ignore[override]
                if not self.stack:
                    for alias in node.names:
                        module_imports.add(alias.asname or alias.name.split(".")[0])
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # type: ignore[override]
                if not self.stack:
                    target_module = _module_from_import(path, node.module or None, node.level) or node.module
                    wildcard = False
                    for alias in node.names:
                        if alias.name == "*":
                            wildcard = True
                            continue
                        module_imports.add(alias.asname or alias.name)
                    if wildcard and target_module:
                        star_imports.add(target_module)
                self.generic_visit(node)

            def visit_Assign(self, node: ast.Assign) -> None:  # type: ignore[override]
                if not self.stack:
                    for tgt in node.targets:
                        module_globals.update(_target_names(tgt))
                self.generic_visit(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # type: ignore[override]
                if not self.stack and node.target:
                    module_globals.update(_target_names(node.target))
                self.generic_visit(node)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:  # type: ignore[override]
                if not self.stack and node.target:
                    module_globals.update(_target_names(node.target))
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
                self.stack.append(node.name)
                qual = ".".join(self.stack)
                params: set[str] = set()
                try:
                    args = node.args
                    for arg in (args.posonlyargs + args.args + args.kwonlyargs):
                        params.add(arg.arg)
                    if args.vararg:
                        params.add(args.vararg.arg)
                    if args.kwarg:
                        params.add(args.kwarg.arg)
                except Exception:
                    pass
                collector = _LocalCollector()
                for stmt in node.body:
                    collector.visit(stmt)
                locals_by_qual[qual] = params | collector.names
                refs_by_qual[qual] = _load_names(node)
                self.generic_visit(node)
                self.stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

        _Analyzer().visit(tree)
        data = {
            "locals": locals_by_qual,
            "imports": module_imports,
            "globals": module_globals,
            "refs": refs_by_qual,
            "star_imports": star_imports,
        }
        analysis_cache[path] = data
        return data

    def _module_defs(module_path: str) -> List[Dict[str, object]]:
        cached = module_defs_cache.get(module_path)
        if cached is not None:
            return cached
        defs: List[Dict[str, object]] = []
        try:
            src = Path(module_path).read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            module_defs_cache[module_path] = defs
            return defs

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append(
                    {
                        "path": module_path,
                        "start": int(getattr(node, "lineno", 1)),
                        "end": int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                        "qualname": node.name,
                        "kind": "def",
                    }
                )
            elif isinstance(node, ast.ClassDef):
                defs.append(
                    {
                        "path": module_path,
                        "start": int(getattr(node, "lineno", 1)),
                        "end": int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                        "qualname": node.name,
                        "kind": "class",
                    }
                )
        module_defs_cache[module_path] = defs
        return defs

    def _expand_star_module(module_name: str) -> None:
        if not module_name or module_name in expanded_star_modules:
            return
        target_path = _lookup_module(module_name)
        if not target_path:
            return
        expanded_star_modules.add(module_name)
        for defn in _module_defs(target_path):
            qual = defn.get("qualname")
            if not qual:
                continue
            simple = qual.split(".")[-1] if isinstance(qual, str) else qual
            entry = dict(defn)
            name_map.setdefault(simple, []).append(entry)
            _add_span(
                target_path,
                entry,
                reason="repair-star-import",
                role="neighbor",
                log_msg=f"repair-star-import: added {simple} from {Path(target_path).name} (star import)",
            )

    def _add_span(path: str, cand: Dict[str, object], *, reason: str, role: str, log_msg: str | None = None) -> None:
        reason = str(reason)
        qual = cand.get("qualname")
        key = (path, qual)
        if key in existing_spans:
            return
        if allowed_set and str(Path(path).resolve()) not in allowed_set:
            return
        tier0.append(
            {
                "path": path,
                "start": int(cand.get("start", 1)),
                "end": int(cand.get("end", 1)),
                "qualname": qual,
                "kind": cand.get("kind"),
                "reason": reason,
                "role": role,
            }
        )
        existing_spans.add(key)
        if log_msg:
            _emit_log(log_msg, role)

    def _add_file(path: str, *, reason: str, role: str, log_msg: str | None = None) -> None:
        if not path:
            return
        if allowed_set and str(Path(path).resolve()) not in allowed_set:
            return
        if path in existing_files:
            return
        reason = str(reason)
        tier1.append(
            {
                "path": path,
                "start": 0,
                "end": 0,
                "qualname": "<FILE>",
                "kind": "file",
                "reason": reason,
                "role": role,
            }
        )
        existing_files.add(path)
        if log_msg:
            _emit_log(log_msg, role)

    def _module_from_import(source: str, module: str | None, level: int) -> str | None:
        try:
            rel = Path(source).resolve().relative_to(root_resolved)
        except Exception:
            base_parts: list[str] = []
        else:
            if rel.name == "__init__.py":
                base_parts = list(rel.parent.parts)
            else:
                base_parts = list(rel.with_suffix("").parts[:-1])
        if level and level > 0:
            drop = min(len(base_parts), max(level - 1, 0))
            if drop:
                base_parts = base_parts[:-drop]
            if level - 1 > len(base_parts):
                base_parts = []
        if module:
            base_parts.extend(part for part in module.split(".") if part)
        dotted = ".".join(part for part in base_parts if part)
        return dotted or module or None

    def _lookup_module(module_name: str | None) -> str | None:
        if not module_name:
            return None
        existing = module_map.get(module_name)
        if existing:
            return existing
        parts = [p for p in module_name.split(".") if p]
        if not parts:
            return None
        base = Path(root_path, *parts)
        py_candidate = base.with_suffix(".py")
        if py_candidate.exists():
            resolved = str(py_candidate)
            module_map[module_name] = resolved
            return resolved
        init_candidate = base / "__init__.py"
        if init_candidate.exists():
            resolved = str(init_candidate)
            module_map[module_name] = resolved
            return resolved
        return None

    if static_messages:
        for line in static_messages:
            match_name = UNDEFINED_NAME_RX.match(line)
            if match_name:
                missing = match_name.group(2)
                source_file = match_name.group("file")
                base_candidates = name_map.get(missing)
                candidates = list(base_candidates) if base_candidates else []
                if not candidates and source_file:
                    analysis = _analysis_for(source_file)
                    star_modules = analysis.get("star_imports", set()) or set()
                    for module_name in star_modules:
                        _expand_star_module(module_name)
                    base_candidates = name_map.get(missing)
                    candidates = list(base_candidates) if base_candidates else []
                added = False
                try:
                    src_text = Path(source_file).read_text(encoding="utf-8")
                except Exception:
                    src_text = ""
                for cand in candidates:
                    cand_path = cand["path"]
                    _add_span(
                        cand_path,
                        cand,
                        reason="repair-undefined",
                        role="repair",
                        log_msg=f"repair-undefined: added span {missing} from {Path(cand_path).name}",
                    )
                    key = (cand_path, cand.get("qualname"))
                    if key in existing_spans:
                        added = True
                        if re.search(rf"\b(from|import)\b[^\n]*\b{re.escape(missing)}\b", src_text):
                            _add_file(
                                cand_path,
                                reason="repair-import",
                                role="neighbor",
                                log_msg=f"repair-import: added module {next((mod for mod, pth in module_map.items() if pth == cand_path), Path(cand_path).stem)}",
                            )
                        break
                if not added:
                    try:
                        src_path = str(Path(source_file).resolve())
                    except Exception:
                        src_path = source_file
                    _add_file(
                        src_path,
                        reason="repair-promote-file",
                        role="repair",
                        log_msg=f"repair-promote-file: promoted file {Path(src_path).name} for undefined name {missing}",
                    )
                continue

            match_mod = MISSING_IMPORT_RX.match(line)
            if match_mod:
                module_name = match_mod.group(2)
                source_file = match_mod.group("file")
                target_path = _lookup_module(module_name)
                if target_path:
                    _add_file(
                        target_path,
                        reason="repair-import",
                        role="neighbor",
                        log_msg=f"repair-import: added module {module_name}",
                    )
                else:
                    try:
                        src_path = str(Path(source_file).resolve())
                    except Exception:
                        src_path = source_file
                    _add_file(
                        src_path,
                        reason="repair-missing-import",
                        role="repair",
                        log_msg=f"repair-missing-import: promoted file {Path(src_path).name} for missing import {module_name}",
                    )

    def _fallback_import_repairs() -> None:
        seen_missing: set[str] = set()
        plan_files = {sp["path"] for sp in tier0 if sp.get("path")}
        plan_files.update(s["path"] for s in tier1 if s.get("path"))
        for file_path in sorted(plan_files):
            imports = index.get(file_path, {}).get("imports") or []
            for imp in imports:
                module = imp.get("module") or ""
                level = int(imp.get("level", 0) or 0)
                abs_module = _module_from_import(file_path, module or None, level)
                if not abs_module:
                    continue
                target_path = _lookup_module(abs_module)
                if target_path:
                    _add_file(
                        target_path,
                        reason="repair-import",
                        role="neighbor",
                        log_msg=f"repair-import: added module {abs_module}",
                    )
                else:
                    if file_path in seen_missing:
                        continue
                    seen_missing.add(file_path)
                    _add_file(
                        file_path,
                        reason="repair-missing-import",
                        role="repair",
                        log_msg=f"repair-missing-import: promoted file {Path(file_path).name} for missing import {abs_module}",
                    )

    def _fallback_name_repairs() -> None:
        promoted_files: set[str] = set()
        span_queue = list(tier0)
        seen_pairs: set[tuple[str, str]] = {
            (sp.get("path"), sp.get("qualname"))
            for sp in tier0
            if sp.get("path") and sp.get("qualname")
        }
        for file_path in sorted(existing_files):
            info = index.get(file_path, {})
            for d in info.get("defs", []) or []:
                qual = d.get("qualname")
                if not qual:
                    continue
                key = (file_path, qual)
                if key in seen_pairs:
                    continue
                span_queue.append({"path": file_path, "qualname": qual})
                seen_pairs.add(key)

        for span in span_queue:
            path = span.get("path")
            qual = span.get("qualname")
            if not path or not qual:
                continue
            info = index.get(path, {})
            refs: set[str] = set()
            info_refs = info.get("refs")
            if isinstance(info_refs, dict):
                refs.update(info_refs.get(qual, set()) or set())
            analysis = _analysis_for(path)
            analysis_refs = analysis.get("refs") if isinstance(analysis, dict) else {}
            if isinstance(analysis_refs, dict):
                refs.update(analysis_refs.get(qual, set()) or set())
            if not refs:
                continue
            func_locals = analysis["locals"].get(qual, set()) if isinstance(analysis.get("locals"), dict) else set()
            module_imports = set(analysis.get("imports", set()))
            module_globals = set(analysis.get("globals", set()))
            file_defs = {d.get("name") for d in info.get("defs", []) or [] if d.get("name")}
            for ref in refs:
                if isinstance(ref, tuple):
                    continue
                name = ref
                if name in builtin_names:
                    continue
                if name in func_locals or name in (module_imports or set()) or name in (module_globals or set()) or name in (file_defs or set()):
                    continue
                candidates = name_map.get(name, [])
                if candidates:
                    # Prefer candidates not in same file when available
                    def _candidate_key(cand: Dict[str, object]) -> tuple[int, int, int, str]:
                        cand_path = Path(str(cand.get("path")))
                        same_dir = int(cand_path.parent == Path(path).parent)
                        same_file = int(str(cand.get("path")) == path)
                        depth = len(cand_path.parts)
                        return (-same_dir, same_file, depth, cand_path.as_posix())

                    best = sorted(candidates, key=_candidate_key)[0]
                    log_msg = f"repair-undefined: added span {name} from {Path(best['path']).name}"
                    _add_span(best["path"], best, reason="repair-undefined", role="repair", log_msg=log_msg)
                    continue
                if path not in promoted_files:
                    promoted_files.add(path)
                    _add_file(
                        path,
                        reason="repair-promote-file",
                        role="repair",
                        log_msg=f"repair-promote-file: promoted file {Path(path).name} for unresolved name {name}",
                    )

    _fallback_import_repairs()
    _fallback_name_repairs()

    # Heuristic dynamic import detection (e.g., __import__ or importlib.import_module)
    scan_paths: set[str] = set(span.get("path") for span in tier0 if span.get("path"))
    scan_paths.update(item.get("path") for item in tier1 if item.get("path"))
    try:
        plan_spans = getattr(plan, "spans", []) if not isinstance(plan, dict) else plan.get("spans", [])
    except Exception:
        plan_spans = []
    for sp in plan_spans or []:
        if isinstance(sp, dict) and sp.get("path"):
            scan_paths.add(sp["path"])
    try:
        plan_full = getattr(plan, "full_files", []) if not isinstance(plan, dict) else plan.get("full_files", [])
    except Exception:
        plan_full = []
    for p in plan_full or []:
        if p:
            scan_paths.add(p)

    if scan_paths:
        detected_modules: set[str] = set()
        for path in scan_paths:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except Exception:
                continue
            detected_modules.update(m.group(1) for m in DYNAMIC_IMPORT_RX.finditer(text))
            detected_modules.update(m.group(1) for m in IMPORTLIB_RX.finditer(text))

        for module_name in sorted(detected_modules):
            target_path = _lookup_module(module_name)
            if not target_path:
                continue
            _add_file(
                target_path,
                reason="repair-dynamic-import",
                role="repair",
                log_msg=f"repair-dynamic-import: added module {module_name} (dynamic import)",
            )

    # 4) Return final plan + logs
    final_plan = _plan_from_tiers(root, tier0, tier1)
    return final_plan, logs


def final_sanity_pass(logs, model_name=None):
    # No-op placeholder; keep API for agent.
    return None, None



def _run_static_check(root: str, tool: str | None = None, allowed_paths: set[str] | None = None) -> list[str]:
    """Run static analysis using pyflakes or pyright if available."""
    root_path = Path(root)
    py_files = sorted(
        str(path) for path in root_path.rglob("*.py")
        if not path.is_symlink() and path.is_file()
        and (allowed_paths is None or str(path.resolve()) in allowed_paths)
    )
    if not py_files:
        return []

    candidates: list[str] = []
    if tool:
        candidates = [tool]
    else:
        for name in ('pyflakes', 'pyright'):
            if shutil.which(name):
                candidates.append(name)
    if not candidates:
        return []

    messages: list[str] = []
    for name in candidates:
        try:
            if name == 'pyflakes':
                proc = subprocess.run([name, *py_files], capture_output=True, text=True, check=False)
                output = proc.stdout.splitlines() + proc.stderr.splitlines()
                messages = [line for line in output if line.strip()]
                if messages:
                    break
            else:
                proc = subprocess.run([name, '--outputjson', *py_files], capture_output=True, text=True, check=False)
                if proc.stdout:
                    messages.append(proc.stdout.strip())
                if proc.stderr:
                    messages.append(proc.stderr.strip())
                if messages:
                    break
        except Exception as exc:
            messages.append(str(exc))
            break
    return messages
