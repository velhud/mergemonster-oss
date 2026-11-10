from codemerge.filemerger.config import AppConfig


def test_save_config_persists_always_include_list(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(str(cfg_path))

    custom = ["readme", "license", "changelog"]
    cfg.config["always_include_docs_list"] = list(custom)
    cfg.save_config()

    reloaded = AppConfig(str(cfg_path)).get_config()
    assert reloaded.get("always_include_docs_list") == custom
