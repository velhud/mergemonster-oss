# Contributing

Thanks for contributing to MergeMonster. Issues and pull requests should focus
on the public repository product and include enough detail to reproduce the
reported behavior.

## Before opening a pull request

- Read the relevant documentation and confirm the behavior against the current
  code.
- Keep changes within the selected workspace and explicit output paths.
- Do not add credentials, private repository data, generated user context,
  screenshots containing private paths, or unrelated code.
- For copied code, assets, parser grammars, or sample data, document the source
  and redistribution terms. Update `THIRD_PARTY_NOTICES.md` when needed.
- Update user-facing docs for changed commands, provider behavior, modes, or
  platform support.

## Development checks

Use Python 3.11 or 3.12 and the development extras. Run focused tests for the
changed area and the deterministic suite before requesting review:

```bash
python -m ruff check codemerge tests --select F821
python -m pytest -q
```

Do not run provider-backed checks against real repositories by default. If a
change requires a live provider test, use synthetic input, explicit scope, and
clearly identify the test as live.

## Pull requests

Complete the repository pull-request template. Describe behavior changes,
verification, limitations, and any new provider or data-flow behavior. A
passing local test does not imply that all supported platforms or provider
configurations have been verified.
