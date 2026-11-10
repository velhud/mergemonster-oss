import os

from codemerge.filemerger.config import AppConfig


def test_config_update_exclusions_preserves_other_keys(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(str(cfg_path))
    cfg.update_config({"always_include_docs_list": ["readme", "license"]})

    raw_abs = tmp_path / "foo" / ".." / "bar"
    cfg.update_exclusions(excluded_dirs=[str(raw_abs)], excluded_files=["a.txt"])

    reloaded = AppConfig(str(cfg_path)).get_config()
    assert reloaded.get("always_include_docs_list") == ["readme", "license"]

    expected_abs = os.path.normcase(os.path.abspath(str(raw_abs)))
    assert expected_abs in reloaded.get("excluded_dirs", [])
    assert "a.txt" in reloaded.get("excluded_files", [])
