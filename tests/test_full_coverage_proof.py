import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codemerge.models import Category, FileMeta
from codemerge.llm.summarize import (
    aggregate_prompt_fingerprint,
    plain_prompt_fingerprint,
    resolve_full_summary_model,
)
from codemerge.summarizer import router
from codemerge.summarizer.shared.audit import serialize_audit_artifact
from codemerge.summarizer.shared.aggregate_cache import AggregateCache
from codemerge.summarizer.full import orchestrator as full_orch


class DummyFP:
    def count_tokens(self, text: str) -> int:
        return max(len(text) // 4, 1)

    def is_binary_file(self, file_path: str, chunk_size: int = 2048) -> bool:
        return False

    def read_file_content(self, file_path: str) -> str:
        return ""


def _make_config(db_path: str) -> dict:
    return {
        "summary_cache_db_path": db_path,
        "full_summary_model": "test-model",
    }


def _valid_folder_summary(*paths: str) -> str:
    path_lines = "\n".join(f"- `{path}`" for path in paths)
    return (
        "# Folder summary\n"
        "## Role in the system\n"
        f"{path_lines}\n"
        "## Structure map\n"
        f"- Coverage: {', '.join(f'`{path}`' for path in paths)}\n"
        "## Interfaces / entry points\n"
        f"{path_lines}\n"
        "## Key flows through this area\n"
        f"- Flow touches {', '.join(f'`{path}`' for path in paths)}.\n"
        "## Dependencies\n"
        "- External services remain generic.\n"
        "## Side effects zones\n"
        "- filesystem\n"
        "## Where to change X\n"
        f"{path_lines}\n"
        "## Uncertainties\n"
        "- None.\n"
        "## Coverage note\n"
        "- Complete.\n"
    )


class FullCoverageProofTests(unittest.TestCase):
    def test_folder_coverage_proof_ok_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "cache.db")
            cfg = _make_config(db_path)
            prompt_fp = plain_prompt_fingerprint("folder", 1)
            model = resolve_full_summary_model(cfg)
            cache = AggregateCache(db_path)
            try:
                folder_path = "/tmp/repo/sub"
                cache.put_ok(
                    "folder",
                    folder_path,
                    manifest_hash="m1",
                    model=model,
                    prompt_fp=prompt_fp,
                    summary_text="Summary",
                    file_count=2,
                    skipped_count=0,
                    reduction_layers=1,
                    batch_count=1,
                    audit_json=serialize_audit_artifact(
                        {
                            "mode": "full",
                            "target_kind": "folder",
                            "target_path": folder_path,
                            "pipeline_version": full_orch.FULL_PIPELINE_VERSION,
                            "runtime_fingerprint": full_orch._full_runtime_fingerprint(cfg),
                        }
                    ),
                )
                proof_ok = router.get_full_coverage_proof(
                    target_kind="folder",
                    target_path=folder_path,
                    app_config=cfg,
                    manifest_hash="m1",
                )
                self.assertIsNotNone(proof_ok)
                self.assertEqual(proof_ok.get("coverage", {}).get("file_count"), 2)

                proof_miss = router.get_full_coverage_proof(
                    target_kind="folder",
                    target_path=folder_path,
                    app_config=cfg,
                    manifest_hash="m2",
                )
                self.assertIsNone(proof_miss)
            finally:
                cache.close()

    def test_repo_coverage_proof_ok_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "cache.db")
            cfg = _make_config(db_path)
            prompt_fp = plain_prompt_fingerprint("repo", 1)
            model = resolve_full_summary_model(cfg)
            cache = AggregateCache(db_path)
            try:
                repo_path = "/tmp/repo"
                cache.put_ok(
                    "repo",
                    repo_path,
                    manifest_hash="r1",
                    model=model,
                    prompt_fp=prompt_fp,
                    summary_text="Summary",
                    file_count=2,
                    skipped_count=0,
                    reduction_layers=1,
                    batch_count=1,
                    audit_json=serialize_audit_artifact(
                        {
                            "mode": "full",
                            "target_kind": "repo",
                            "target_path": repo_path,
                            "pipeline_version": full_orch.FULL_PIPELINE_VERSION,
                            "runtime_fingerprint": full_orch._full_runtime_fingerprint(cfg),
                        }
                    ),
                )
                proof_ok = router.get_full_coverage_proof(
                    target_kind="repo",
                    target_path=repo_path,
                    app_config=cfg,
                    manifest_hash="r1",
                )
                self.assertIsNotNone(proof_ok)
                self.assertEqual(proof_ok.get("coverage", {}).get("file_count"), 2)

                proof_miss = router.get_full_coverage_proof(
                    target_kind="repo",
                    target_path=repo_path,
                    app_config=cfg,
                    manifest_hash="r2",
                )
                self.assertIsNone(proof_miss)
            finally:
                cache.close()

    def test_repo_coverage_proof_selects_requested_manifest_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "cache.db")
            cfg = _make_config(db_path)
            prompt_fp = plain_prompt_fingerprint("repo", 1)
            model = resolve_full_summary_model(cfg)
            cache = AggregateCache(db_path)
            try:
                repo_path = "/tmp/repo"
                for manifest_hash, summary_text, file_count in (
                    ("r1", "Summary A", 2),
                    ("r2", "Summary B", 3),
                ):
                    cache.put_ok(
                        "repo",
                        repo_path,
                        mode="full",
                        manifest_hash=manifest_hash,
                        model=model,
                        prompt_fp=prompt_fp,
                        summary_text=summary_text,
                        file_count=file_count,
                        skipped_count=0,
                        reduction_layers=1,
                        batch_count=1,
                        audit_json=serialize_audit_artifact(
                            {
                                "mode": "full",
                                "target_kind": "repo",
                                "target_path": repo_path,
                                "pipeline_version": full_orch.FULL_PIPELINE_VERSION,
                                "runtime_fingerprint": full_orch._full_runtime_fingerprint(cfg),
                            }
                        ),
                    )
                proof_r1 = router.get_full_coverage_proof(
                    target_kind="repo",
                    target_path=repo_path,
                    app_config=cfg,
                    manifest_hash="r1",
                )
                proof_r2 = router.get_full_coverage_proof(
                    target_kind="repo",
                    target_path=repo_path,
                    app_config=cfg,
                    manifest_hash="r2",
                )
                self.assertEqual(proof_r1.get("manifest_hash"), "r1")
                self.assertEqual(proof_r1.get("coverage", {}).get("file_count"), 2)
                self.assertEqual(proof_r2.get("manifest_hash"), "r2")
                self.assertEqual(proof_r2.get("coverage", {}).get("file_count"), 3)
            finally:
                cache.close()

    def test_manifest_hash_changes_with_metadata(self) -> None:
        base = Path("/tmp/repo")
        meta_a = FileMeta(
            abs_path=base / "a.py",
            rel_path=Path("a.py"),
            size_bytes=10,
            mtime=1.0,
            token_count=1,
            ext=".py",
            category=Category.BACKEND,
        )
        meta_b = FileMeta(
            abs_path=base / "a.py",
            rel_path=Path("a.py"),
            size_bytes=11,
            mtime=1.0,
            token_count=1,
            ext=".py",
            category=Category.BACKEND,
        )
        _paths_a, hash_a = full_orch._build_manifest([meta_a])
        _paths_b, hash_b = full_orch._build_manifest([meta_b])
        self.assertNotEqual(hash_a, hash_b)

    def test_full_summary_tracks_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(os.path.join(tmpdir, "cache.db"))
            cfg["summary_prompt_version"] = getattr(full_orch, "PLAIN_SUMMARY_VERSION", 1)
            base = Path("/tmp/repo")
            meta = FileMeta(
                abs_path=base / "bad.txt",
                rel_path=Path("bad.txt"),
                size_bytes=10,
                mtime=1.0,
                token_count=1,
                ext=".txt",
                category=Category.DOCS,
                read_error=True,
            )
            with mock.patch.object(full_orch, "fetch_ai_text_summary", return_value=_valid_folder_summary("bad.txt")):
                _summary = full_orch.ensure_folder_summary_full(
                    folder_abs=str(base),
                    root_abs=str(base),
                    scan_files=[meta],
                    file_processor=DummyFP(),
                    app_config=cfg,
                )
            proof = router.get_full_coverage_proof(
                target_kind="folder",
                target_path=str(base),
                app_config=cfg,
            )
            self.assertIsNotNone(proof)
            self.assertEqual(proof.get("coverage", {}).get("skipped_count"), 1)
            self.assertEqual(
                proof.get("coverage", {}).get("skipped_items"),
                [{"path": "bad.txt", "reason": "meta_read_error"}],
            )

    def test_full_coverage_proof_surfaces_audit_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "cache.db")
            cfg = _make_config(db_path)
            cfg["summary_prompt_version"] = 9
            model = resolve_full_summary_model(cfg)
            prompt_fp = aggregate_prompt_fingerprint("repo", "full", cfg["summary_prompt_version"])
            cache = AggregateCache(db_path)
            try:
                repo_path = os.path.join(tmpdir, "repo")
                cache.put_ok(
                    "repo",
                    repo_path,
                    mode="full",
                    manifest_hash="manifest-1",
                    inputs_hash="inputs-1",
                    inventory_hash="inventory-1",
                    model=model,
                    prompt_fp=prompt_fp,
                    summary_text="candidate summary",
                    file_count=4,
                    skipped_count=1,
                    reduction_layers=2,
                    batch_count=3,
                    group_counts="2,1",
                    inventory_count=6,
                    scope_label="text_only_excluding_binary_assets",
                    scope_definition_version=1,
                    audit_json=serialize_audit_artifact(
                        {
                            "mode": "full",
                            "target_kind": "repo",
                            "target_path": repo_path,
                            "scope_source": "aggregate_scan",
                            "pipeline_version": full_orch.FULL_PIPELINE_VERSION,
                            "runtime_fingerprint": full_orch._full_runtime_fingerprint(cfg),
                            "manifest_hash": "manifest-1",
                            "inputs_hash": "inputs-1",
                            "inventory_hash": "inventory-1",
                            "manifest_paths": ["src/a.py", "src/b.py"],
                            "skipped_items": [{"path": "assets/logo.png", "reason": "binary_or_asset_ext"}],
                            "inventory_items": ["src/", "README.md"],
                            "candidate_summary": "candidate summary",
                            "final_summary": "final summary",
                            "selected_evidence_paths": ["src/a.py", "src/b.py"],
                            "reduction_plan": [{"layer": 1, "group_count": 2, "group_sizes": [3, 2]}],
                            "provider_calls": {"aggregate_total": 4},
                            "validation": {
                                "inventory_covered": True,
                                "missing_headings": [],
                                "placeholder_leaks": [],
                                "uncovered_inventory_items": [],
                            },
                        }
                    ),
                )

                proof = router.get_full_coverage_proof(
                    target_kind="repo",
                    target_path=repo_path,
                    app_config=cfg,
                    manifest_hash="manifest-1",
                )

                self.assertIsNotNone(proof)
                assert proof is not None
                coverage = proof["coverage"]
                audit = proof["audit"]
                self.assertEqual(coverage["inputs_hash"], "inputs-1")
                self.assertEqual(coverage["inventory_hash"], "inventory-1")
                self.assertEqual(coverage["inventory_count"], 6)
                self.assertEqual(coverage["scope"], "text_only_excluding_binary_assets")
                self.assertEqual(coverage["scope_definition_version"], 1)
                self.assertEqual(coverage["group_counts"], [2, 1])
                self.assertEqual(coverage["manifest_paths"], ["src/a.py", "src/b.py"])
                self.assertEqual(
                    coverage["skipped_items"],
                    [{"path": "assets/logo.png", "reason": "binary_or_asset_ext"}],
                )
                self.assertEqual(coverage["inventory_items"], ["src/", "README.md"])
                self.assertEqual(coverage["reduction_plan"], [{"layer": 1, "group_count": 2, "group_sizes": [3, 2]}])
                self.assertEqual(
                    coverage["validation"],
                    {
                        "inventory_covered": True,
                        "missing_headings": [],
                        "placeholder_leaks": [],
                        "uncovered_inventory_items": [],
                    },
                )
                self.assertEqual(audit["scope_source"], "aggregate_scan")
                self.assertEqual(audit["candidate_summary"], "candidate summary")
                self.assertEqual(audit["final_summary"], "final summary")
                self.assertEqual(audit["selected_evidence_paths"], ["src/a.py", "src/b.py"])
            finally:
                cache.close()

    def test_full_coverage_proof_rejects_stale_runtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "cache.db")
            cfg = _make_config(db_path)
            cfg["summary_prompt_version"] = 9
            model = resolve_full_summary_model(cfg)
            prompt_fp = aggregate_prompt_fingerprint("repo", "full", cfg["summary_prompt_version"])
            cache = AggregateCache(db_path)
            try:
                repo_path = os.path.join(tmpdir, "repo")
                cache.put_ok(
                    "repo",
                    repo_path,
                    mode="full",
                    manifest_hash="manifest-1",
                    inputs_hash="inputs-1",
                    inventory_hash="inventory-1",
                    model=model,
                    prompt_fp=prompt_fp,
                    summary_text="candidate summary",
                    file_count=4,
                    skipped_count=1,
                    reduction_layers=2,
                    batch_count=3,
                    group_counts="2,1",
                    inventory_count=6,
                    scope_label="text_only_excluding_binary_assets",
                    scope_definition_version=1,
                    audit_json=serialize_audit_artifact(
                        {
                            "mode": "full",
                            "target_kind": "repo",
                            "target_path": repo_path,
                            "pipeline_version": max(0, full_orch.FULL_PIPELINE_VERSION - 1),
                            "runtime_fingerprint": full_orch._full_runtime_fingerprint(cfg),
                        }
                    ),
                )

                proof = router.get_full_coverage_proof(
                    target_kind="repo",
                    target_path=repo_path,
                    app_config=cfg,
                )

                self.assertIsNone(proof)
            finally:
                cache.close()


if __name__ == "__main__":
    unittest.main()
