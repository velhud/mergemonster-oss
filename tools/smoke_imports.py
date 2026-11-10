"""Import the primary application boundaries and verify installed resources."""

from __future__ import annotations

from importlib import import_module, resources
import sys


MODULES = (
    "codemerge.cli",
    "codemerge.core.tokens",
    "codemerge.filemerger",
    "codemerge.filemerger.context_pack",
    "codemerge.structure",
    "codemerge.summarizer",
    "codemerge.jsui.server",
)
RESOURCES = (
    ("codemerge.jsui", "static/index.html"),
    ("codemerge.jsui", "static/app.js"),
    ("codemerge.summarizer", "prompts/system.md"),
    ("codemerge.prune", "assets/schema_prune_plan.json"),
    ("codemerge.core", "data/o200k_base.tiktoken.gz"),
)


def main() -> int:
    for name in MODULES:
        import_module(name)
    for package, relative_name in RESOURCES:
        resource = resources.files(package).joinpath(*relative_name.split("/"))
        if not resource.is_file():
            raise RuntimeError(f"Required installed package resource is missing: {relative_name}")
    from codemerge.core.tokens import count_tokens

    if count_tokens("MergeMonster offline import smoke") <= 0:
        raise RuntimeError("The packaged tokenizer returned an empty count for non-empty input")
    print(f"Imported {len(MODULES)} application modules and verified {len(RESOURCES)} resources.")
    print("Offline tokenizer smoke passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Import smoke failed: {exc}", file=sys.stderr)
        raise
