"""Guard against repository-path hacks that break installed-package imports."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "codemerge"


def main() -> int:
    failures: list[str] = []
    for source in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        except (OSError, SyntaxError) as exc:
            failures.append(f"{source.relative_to(ROOT)}: cannot parse: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"append", "insert", "extend"}
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "path"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "sys"
            ):
                failures.append(
                    f"{source.relative_to(ROOT)}:{node.lineno}: mutates sys.path"
                )

    if not (PACKAGE / "cli.py").is_file():
        failures.append("codemerge/cli.py is missing")
    if not (ROOT / "pyproject.toml").is_file():
        failures.append("pyproject.toml is missing")

    if failures:
        print("Import guard failed:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in failures), file=sys.stderr)
        return 1
    print(f"Import guard passed for {len(list(PACKAGE.rglob('*.py')))} package modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
