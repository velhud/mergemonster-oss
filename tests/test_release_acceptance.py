from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codemerge.core.tokens import count_tokens
from codemerge.filemerger import FileProcessor
from codemerge.filemerger.extras.compare_ai import compare_codebases_ai
from codemerge.filemerger.output import atomic_text_output
from codemerge.jsui.server import app


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "synthetic-repository"


def test_mixed_context_round_trip_and_freshness(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-repository"
    shutil.copytree(EXAMPLE, root)
    (root / "binary.py").write_bytes(b"\x00\x01\x02\xff")
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE_WORKSPACE = True\n")
    (root / "escape.py").symlink_to(outside)
    (root / "escape_dir").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv("CODEMERGE_REQUIRE_CSRF", "1")

    def unexpected_provider(*args, **kwargs):
        pytest.fail("the offline merge contacted a provider")

    monkeypatch.setattr("codemerge.llm._summarize_service.get_groq_client", unexpected_provider)

    with TestClient(app) as client:
        token = client.get("/api/session").json()["csrf_token"]
        headers = {"X-CodeMerge-CSRF": token}
        assert client.get("/api/session", headers={"Host": "outside.example"}).status_code == 400
        assert client.get("/api/session", headers={"Origin": "https://outside.example"}).status_code == 403
        assert client.post("/api/scan", json={"path": str(root)}).status_code == 403

        scan_args = {"path": str(root), "include_all": True, "ignore_binary": True, "follow_symlinks": True}
        scan = client.post("/api/scan", json=scan_args, headers=headers)
        assert scan.status_code == 200, scan.text
        scanned = {item["rel_path"] for item in scan.json()["files"]}
        assert {"src/service.py", "src/client.ts", "src/worker.go", "docs/overview.md", "src/settings.toml"} <= scanned
        assert {"vendor/sample_dependency.py", "generated/build_info.py"} <= scanned
        assert not {"binary.py", "escape.py"} & scanned
        assert not any(path.startswith("escape_dir/") for path in scanned)

        plan = {
            "docs/overview.md": "CODE",
            "src/client.ts": "CODE_PLUS_SKELETON",
            "src/invalid_syntax.py": "SKELETON",
            "src/service.py": "SKELETON",
            "src/settings.toml": "EXCLUDE",
            "src/worker.go": "SKELETON",
            "vendor/sample_dependency.py": "EXCLUDE",
            "generated/build_info.py": "EXCLUDE",
        }
        for path, mode in plan.items():
            response = client.put("/api/modes", json={
                "path": str(root), "selected_paths": [path], "mode": mode,
            }, headers=headers)
            assert response.status_code == 200, response.text

        selection = sorted(plan)
        payload = {"path": str(root), "selected_paths": selection}
        preview = client.post("/api/merge/preview", json=payload, headers=headers).json()
        text = preview["text"]
        assert "# SKELETON" in text
        assert str(root) not in text and "OUTSIDE_WORKSPACE" not in text
        assert "vendor/sample_dependency.py" not in text
        assert "generated/build_info.py" not in text
        assert "invalid_syntax.py" in text and "syntax" in text.lower()

        output = root / "demo.txt"
        export_args = {**payload, "output_file": output.name, "create_json_manifest": True}
        result = client.post("/api/merge", json=export_args, headers=headers)
        assert result.status_code == 200, result.text
        assert output.read_text() == text
        assert preview["tokens_total"] == result.json()["token_count"] == count_tokens(text)
        manifest_text = (root / "demo.txt.manifest.json").read_text()
        manifest = json.loads(manifest_text)
        assert manifest["root_folder"] == "." and str(root) not in manifest_text
        assert [entry["relative_path"] for entry in manifest["merged_files"]] == [
            path for path in selection if plan[path] != "EXCLUDE"
        ]
        assert client.post("/api/merge", json=export_args, headers=headers).status_code == 409
        assert output.read_text() == text

        before = client.get("/api/watch/fingerprint", params={"path": str(root)}).json()
        edited = root / "src/service.py"
        edited.write_text(edited.read_text() + "\n\ndef fresh_signature():\n    return 'fresh'\n")
        new_mtime = max(before["max_mtime"] + 2, edited.stat().st_mtime + 2)
        os.utime(edited, (new_mtime, new_mtime))
        after = client.get("/api/watch/fingerprint", params={"path": str(root)}).json()
        assert after["max_mtime"] > before["max_mtime"]

        updated = client.post("/api/merge/preview", json=payload, headers=headers).json()["text"]
        assert "fresh_signature" in updated and updated != text
        for path in ("docs/overview.md", "src/client.ts", "src/worker.go"):
            start = f"======= START OF FILE: '{path}' ======="
            end = f"======= END OF FILE: '{path}' ======="
            assert text.split(start)[1].split(end)[0] == updated.split(start)[1].split(end)[0]

        exported = client.post("/api/session/export", json={"selected_paths": selection}, headers=headers)
        session = exported.json()["session"]
        assert client.post("/api/session/import", json={"session": session}, headers=headers).status_code == 200
        assert client.post("/api/modes/reset", json={}, headers=headers).status_code == 200
        assert client.post("/api/session/select", json={"session": session}, headers=headers).status_code == 200
        reopened = client.post("/api/merge/preview", json=payload, headers=headers).json()["text"]
        assert reopened == updated


def test_pairing_does_not_read_outside_file(tmp_path, monkeypatch):
    left, right, output = (tmp_path / name for name in ("left", "right", "output"))
    left.mkdir()
    right.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside sentinel\n")
    (left / "match.py").symlink_to(outside)
    (right / "match.py").write_text("right contents\n")

    def fail_if_called(*args, **kwargs):
        pytest.fail("outside file reached the provider comparison")

    monkeypatch.setattr("codemerge.llm.summarize.compare_files_ai", fail_if_called)
    processor = FileProcessor()
    assert compare_codebases_ai(processor, str(left), str(right)) == {"match.py": "ONLY_B"}
    processor.pair_codebases(str(left), str(right), str(output))
    assert "outside sentinel" not in "".join(path.read_text() for path in output.rglob("*.txt"))


def test_interrupted_text_export_keeps_existing_file_and_cleans_staging(tmp_path):
    destination = tmp_path / "context.txt"
    destination.write_text("previous output")
    with pytest.raises(RuntimeError, match="interrupted"):
        with atomic_text_output(destination) as handle:
            handle.write("partial output")
            raise RuntimeError("interrupted")
    assert destination.read_text() == "previous output"
    assert list(tmp_path.glob(".mergemonster-*.tmp")) == []
