from __future__ import annotations

import json
from pathlib import Path


def test_public_documentation_covers_product_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    required = (
        "README.md",
        "docs/architecture.md",
        "docs/demo.md",
        "docs/privacy-and-data-flow.md",
        "docs/provider-configuration.md",
        "docs/context-formats.md",
        "docs/development.md",
        "docs/troubleshooting.md",
        "examples/synthetic-repository/README.md",
    )
    for relative_path in required:
        assert (root / relative_path).is_file(), relative_path

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Five-minute offline demo" in readme
    assert "independent open-source project" in readme
    assert "docs/privacy-and-data-flow.md" in readme

    provider = (root / "docs/provider-configuration.md").read_text(encoding="utf-8")
    for term in ("GROQ_API_KEY", "groq_summary_model", "openai/gpt-oss-20b", "`.env` files"):
        assert term in provider
    assert "OPENAI_API_KEY" in provider

    privacy = (root / "docs/privacy-and-data-flow.md").read_text(encoding="utf-8")
    for term in ("local", "selected source", "provider preflight", "loopback"):
        assert term in privacy


def test_synthetic_example_is_source_only_and_generates_binary_on_demand() -> None:
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / "synthetic-repository"
    expected = (
        "src/service.py",
        "src/client.ts",
        "src/worker.go",
        "src/invalid_syntax.py",
        "docs/overview.md",
        "src/settings.toml",
        "generated/build_info.py",
        "vendor/sample_dependency.py",
        "ai/MAP.md",
        "ai/manifest.yml",
        "make_binary_fixture.py",
    )
    for relative_path in expected:
        assert (example / relative_path).is_file(), relative_path

    assert not (example / "assets" / "tiny-fixture.bin").exists()
    config = json.loads((example / "config" / "app.example.json").read_text(encoding="utf-8"))
    assert "groq_summary_model" in config
    assert "api_key" not in " ".join(config).lower()
