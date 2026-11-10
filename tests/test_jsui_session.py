from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _scan_context(jsserver, root: Path, file_path: Path):
    meta = jsserver.FileMeta(
        abs_path=file_path.resolve(),
        rel_path=file_path.relative_to(root),
        size_bytes=file_path.stat().st_size,
        mtime=file_path.stat().st_mtime,
        token_count=1,
        ext=file_path.suffix,
        category=jsserver.Category.BACKEND,
    )
    return jsserver.ScanContext(
        root=root.resolve(),
        files=[meta],
        aggregate_tokens=1,
        aggregate_bytes=meta.size_bytes,
        by_ext={file_path.suffix: [meta.size_bytes]},
        by_category={jsserver.Category.BACKEND: [meta.size_bytes]},
    )


def test_session_current_and_export_include_workspace_state(tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    file_path = root / "a.py"
    file_path.write_text("print('a')\n", encoding="utf-8")

    srv = jsserver._init_global_ctx()
    srv.config.update_config({"last_directory": str(root), "last_filters": {"name_pattern": "*.py"}})
    srv.core.configure(srv.config.get_config())
    srv.core.scan_ctx = _scan_context(jsserver, root, file_path)
    srv.core.visible_files = list(srv.core.scan_ctx.files)
    srv.core.per_file_modes = {str(file_path.resolve()): jsserver.FileMode.SUMMARY_LONG}

    client = TestClient(jsserver.app)
    current = client.get("/api/session/current").json()["session"]
    assert current["root"] == str(root.resolve())
    assert current["scan"]["file_count"] == 1
    assert current["scan"]["visible_paths"] == [str(file_path.resolve())]
    assert current["modes"]["per_file_modes"][str(file_path.resolve())] == "SUMMARY_LONG"
    assert current["config"]["last_filters"]["name_pattern"] == "*.py"

    exported = client.post(
        "/api/session/export",
        json={"selected_paths": [str(file_path)], "hidden_paths": ["a.py"]},
    ).json()["session"]
    assert exported["selection"]["selected_paths"] == [str(file_path.resolve())]
    assert exported["selection"]["hidden_paths"] == [str(file_path.resolve())]
    assert exported["diagnostics"] == {}


def test_session_import_reports_out_of_root_and_missing_paths(tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    sibling = tmp_path / "other.py"
    root.mkdir()
    sibling.write_text("print('x')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/session/import",
        json={
            "session": {
                "version": 1,
                "root": str(root),
                "selection": {
                    "selected_paths": [str(root / "missing.py"), str(sibling)],
                },
            }
        },
    )

    assert res.status_code == 409
    diagnostics = res.json()["diagnostics"]
    assert diagnostics["missing_paths"] == [str((root / "missing.py").resolve())]
    assert diagnostics["out_of_root_paths"] == [str(sibling.resolve())]


def test_session_select_applies_config_and_modes(tmp_path) -> None:
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    file_path = root / "a.py"
    file_path.write_text("print('a')\n", encoding="utf-8")

    srv = jsserver._init_global_ctx()
    client = TestClient(jsserver.app)
    res = client.post(
        "/api/session/select",
        json={
            "session": {
                "version": 1,
                "root": str(root),
                "config": {
                    "last_directory": str(root),
                    "last_filters": {"name_pattern": "*.py"},
                    "include_repo_summary_in_merge": True,
                },
                "modes": {
                    "per_file_modes": {str(file_path.resolve()): "SUMMARY_SHORT"},
                    "per_folder_modes": {str(root.resolve()): "FOLDER_SUMMARY_QUICK"},
                },
            }
        },
    )

    assert res.status_code == 200
    assert res.json()["ok"] is True
    cfg = srv.config.get_config()
    assert cfg["last_directory"] == str(root)
    assert cfg["last_filters"]["name_pattern"] == "*.py"
    assert cfg["include_repo_summary_in_merge"] is True
    assert srv.core.get_mode(str(file_path)) is jsserver.FileMode.SUMMARY_SHORT
    assert srv.core.get_folder_mode(str(root)) == "FOLDER_SUMMARY_QUICK"
