from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from codemerge.filemerger.config import AppConfig
from codemerge.jsui.server import _ensure_config_for_path


def test_app_config_defaults_to_codemerge_home(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "codemerge-home"
    monkeypatch.setenv("CODEMERGE_HOME", str(home))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    cfg = AppConfig()

    assert cfg.config_file == str((home / "settings.json").resolve())


def test_save_config_does_not_rewrite_when_payload_is_unchanged(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.json"
    cfg = AppConfig(config_file=str(config_path))
    cfg.save_config()
    original_mtime_ns = config_path.stat().st_mtime_ns

    cfg.save_config()

    assert config_path.stat().st_mtime_ns == original_mtime_ns


def test_project_switch_does_not_rebind_to_repo_local_config(tmp_path: Path) -> None:
    isolated_config = tmp_path / "isolated" / "settings.json"
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    ctx = SimpleNamespace(
        config=AppConfig(config_file=str(isolated_config)),
        config_lock=threading.Lock(),
        core=SimpleNamespace(per_file_modes={}, custom_excluded=set()),
    )

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "settings.json").write_text('{"legacy": true}', encoding="utf-8")

    _ensure_config_for_path(str(project_root), ctx=ctx)

    assert ctx.config.config_file == str(isolated_config.resolve())
    assert ctx.config.get_config()["last_directory"] == str(project_root.resolve())


def test_settings_upgrade_preserves_previous_release_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MERGEMONSTER_HOME", str(tmp_path))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    previous = tmp_path / "pythonmerge_config.json"
    original = '{"show_totals_columns": true}'
    previous.write_text(original, encoding="utf-8")

    config = AppConfig()

    assert config.config_file == str(tmp_path / "settings.json")
    assert config.get_config()["show_totals_columns"] is True
    assert previous.read_text(encoding="utf-8") == original
    config.update_config({"show_totals_columns": False})
    assert AppConfig().get_config()["show_totals_columns"] is False
