"""Project-wide pytest configuration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

def pytest_configure(config):
    """Keep module-import state and all provider credentials out of the suite."""
    temporary_home = tempfile.TemporaryDirectory(prefix="mergemonster-tests-")
    overrides = pytest.MonkeyPatch()
    overrides.setenv("HOME", temporary_home.name)
    overrides.setenv("USERPROFILE", temporary_home.name)
    for key in ("MERGEMONSTER_HOME", "CODEMERGE_HOME", "CODEMERGE_CONFIG_PATH", "CODEMERGE_CONFIG"):
        overrides.delenv(key, raising=False)
    if os.environ.get("CODEMERGE_ALLOW_LIVE_TESTS") != "1":
        for key in ("GROQ_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            overrides.delenv(key, raising=False)
    config.add_cleanup(temporary_home.cleanup)
    config.add_cleanup(overrides.undo)


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    """
    Redirect Path.home() so on-disk caches/dbs land in a temp area.
    Avoids touching the real home directory during tests.
    """

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    yield
