# Development

## Environment

Use Python 3.11 or 3.12 as declared in `pyproject.toml`. Install the development
and desktop extras for local UI work.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,desktop]'
```

The desktop command opens the native pywebview window. The server-only command
is `mergemonster --serve --port 8765`; it binds to `127.0.0.1` and does not open
a browser. The host is fixed to loopback.

## Deterministic checks

Offline checks should not call a provider or require a credential. The current
suite includes unit and API contract tests, scan and exclusion tests, structural
extraction tests, cache and freshness tests, estimator parity, path policy,
session-token, job, and context-pack tests.

```bash
python -m ruff check codemerge tests --select F821
python -m pytest -q
```

The Makefile groups the common development and verification commands.
Release CI verifies the supported platforms and Python versions.

## Provider-backed checks

Live requests are outside the default offline test path. If validating a live
provider, use a synthetic repository, explicit Groq model, bounded scope, and a
test credential in the process environment. Record clearly whether the request
was a mock or live call. This documentation does not assert a live request was
made.

## Example repository

`examples/synthetic-repository` contains only synthetic source. Run
`python examples/synthetic-repository/make_binary_fixture.py` to create its
small binary fixture locally. Open the directory in the desktop application to
exercise scanning, mixed modes, structural output, token preview, merging, and
source-freshness behavior without a provider account.

For a reproducible terminal walkthrough through the installed public API,
install the package and run `python examples/run_offline_demo.py --scratch
<temporary-data-directory>`. The script copies the fixture tree, starts
`mergemonster --serve`, sends local API requests with the session token,
inspects token and merge output, changes a source file, and removes the
temporary workspace and application state on exit. It requires an existing
temporary directory and never uses a repository or user-data workspace as its
scratch location.
