# MergeMonster

MergeMonster is a local-first desktop application for building repository context.
The Python package lives in `codemerge/`. The desktop launcher is
`codemerge/desktop.py` and the local interface is in `codemerge/jsui/`.

- Keep the default workflow usable offline. Require explicit confirmation before
  sending source to a provider.
- Preserve loopback binding, workspace path checks, and safe output writes.
- Use synthetic repositories and mocked providers for tests and examples.
- Run `python -m pytest -q -m 'not live'` and
  `python -m ruff check codemerge tests --select F821` before submitting changes.
