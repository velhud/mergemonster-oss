from __future__ import annotations

from pathlib import Path

from codemerge.filemerger.context_pack import MAX_FILE_BYTES, build_pack


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _setup_repo(
    tmp_path: Path,
    *,
    include_contract_tests: bool = True,
    include_gamma: bool = False,
    unresolved: bool = False,
    include_binary: bool = False,
    include_large: bool = False,
    manifest_lists: bool = False,
) -> Path:
    repo = tmp_path / "repo"
    _write(repo / "ai" / "MAP.md", "map\n")

    subsystems = [
        ("alpha", "alpha", "alpha/public_api.py"),
        ("beta", "beta", "beta/public_api.py"),
    ]
    if include_gamma:
        subsystems.append(("gamma", "gamma", "gamma/public_api.py"))

    manifest_lines = [
        "version: 1",
        "project: Example",
        "core_shared:",
        "  - core_shared/shared.py",
        "subsystems:",
    ]
    for name, root, public_api in subsystems:
        manifest_lines.append(f"  - name: {name}")
        if manifest_lists:
            manifest_lines.extend(
                [
                    "    root:",
                    f"      - {root}",
                    "    public_api:",
                    f"      - {public_api}",
                ]
            )
        else:
            manifest_lines.extend(
                [
                    f"    root: {root}",
                    f"    public_api: {public_api}",
                ]
            )

    _write(repo / "ai" / "manifest.yml", "\n".join(manifest_lines) + "\n")
    _write(repo / "docs" / "CONTRACT.md", "contract\n")

    _write(repo / "core_shared" / "shared.py", "SHARED = 1\n")

    _write(repo / "alpha" / "__init__.py", "")
    _write(repo / "alpha" / "public_api.py", "def api():\n    return 'alpha'\n")
    alpha_mod_lines = [
        "from beta.public_api import api as beta_api",
        "",
        "def use():",
        "    return beta_api()",
    ]
    if unresolved:
        alpha_mod_lines.insert(0, "import missing_pkg")
    _write(repo / "alpha" / "mod.py", "\n".join(alpha_mod_lines) + "\n")
    _write(repo / "alpha" / "notes.txt", "alpha notes\n")

    _write(repo / "beta" / "__init__.py", "")
    beta_public_lines = [
        "from beta.policy import POLICY",
        "",
        "def api():",
        "    return POLICY",
    ]
    if include_gamma:
        beta_public_lines.insert(0, "from gamma.public_api import api as gamma_api")
    _write(repo / "beta" / "public_api.py", "\n".join(beta_public_lines) + "\n")
    _write(repo / "beta" / "policy.py", "POLICY = 'beta'\n")
    _write(repo / "beta" / "internal.py", "def internal():\n    return 1\n")

    if include_gamma:
        _write(repo / "gamma" / "__init__.py", "")
        _write(
            repo / "gamma" / "public_api.py",
            "from gamma.policy import POLICY\n\n"
            "def api():\n"
            "    return POLICY\n",
        )
        _write(repo / "gamma" / "policy.py", "POLICY = 'gamma'\n")
        _write(repo / "gamma" / "internal.py", "def internal():\n    return 2\n")

    if include_contract_tests:
        _write(
            repo / "tests" / "contract" / "alpha" / "test_alpha.py",
            "def test_contract():\n    assert True\n",
        )

    if include_binary:
        _write_bytes(repo / "alpha" / "binary.py", b"\xff\xfe\xfd")

    if include_large:
        _write_bytes(repo / "alpha" / "large.txt", b"a" * (MAX_FILE_BYTES + 1))

    return repo


def test_pack_deterministic_ordering(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, include_contract_tests=True, include_gamma=True)
    result = build_pack(str(repo), "alpha", "audit")
    result2 = build_pack(str(repo), "alpha", "audit")
    assert result.output_text == result2.output_text

    included = list(result.included_files)
    assert included[:5] == [
        "ai/MAP.md",
        "ai/manifest.yml",
        "docs/CONTRACT.md",
        "core_shared/shared.py",
        "alpha/public_api.py",
    ]
    assert included.index("beta/public_api.py") < included.index("beta/policy.py")
    assert included.index("beta/policy.py") < included.index("gamma/public_api.py")
    assert included.index("gamma/public_api.py") < included.index("gamma/policy.py")
    assert included[-1].startswith("tests/contract/alpha/")


def test_manifest_normalization_string_vs_list(tmp_path: Path) -> None:
    repo_a = _setup_repo(tmp_path / "a", manifest_lists=False)
    repo_b = _setup_repo(tmp_path / "b", manifest_lists=True)
    result_a = build_pack(str(repo_a), "alpha", "subsystem")
    result_b = build_pack(str(repo_b), "alpha", "subsystem")
    assert result_a.included_files == result_b.included_files


def test_audit_includes_dependency_public_api_and_closure_only(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, include_gamma=False)
    result = build_pack(str(repo), "alpha", "audit")
    included = set(result.included_files)
    assert "beta/public_api.py" in included
    assert "beta/policy.py" in included
    assert "beta/internal.py" not in included


def test_audit_transitive_dependency_includes_public_api_closure(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, include_gamma=True)
    result = build_pack(str(repo), "alpha", "audit")
    included = set(result.included_files)
    assert "gamma/public_api.py" in included
    assert "gamma/policy.py" in included


def test_unresolved_imports_warn(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, include_gamma=False, unresolved=True)
    result = build_pack(str(repo), "alpha", "audit")
    assert any("Unresolved import in alpha/mod.py" in w for w in result.warnings)


def test_binary_and_large_files_skipped_with_warning(tmp_path: Path) -> None:
    repo = _setup_repo(
        tmp_path,
        include_gamma=False,
        include_binary=True,
        include_large=True,
    )
    result = build_pack(str(repo), "alpha", "subsystem")
    warnings = "\n".join(result.warnings)
    assert "Skipped non-UTF8 (binary) file: alpha/binary.py" in warnings
    assert "Skipped large file (>512KB): alpha/large.txt" in warnings
    assert "alpha/binary.py" not in result.included_files
    assert "alpha/large.txt" not in result.included_files


def test_missing_contract_tests_warn(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, include_contract_tests=False)
    result = build_pack(str(repo), "alpha", "subsystem")
    assert any("Missing contract tests folder" in w for w in result.warnings)
