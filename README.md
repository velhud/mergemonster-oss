# MergeMonster

MergeMonster helps you prepare repository context for code review and AI
workflows. It scans a local repository, lets you choose how each file should be
represented, previews the token impact, and writes a reproducible merged
context file. Files can be kept as source, reduced to a structural view,
represented by a summary, or combined where supported.

The desktop application is local-first. Scanning, structural extraction, token
preview, and context export run locally. Provider-backed summaries are optional
and require an explicit provider and model selection. MergeMonster is an
independent open-source project.

## What it does

- Scan a repository and review why files are included or skipped.
- Choose a per-file representation: excluded, source, short or long summary,
  skeleton, and supported combinations.
- Extract Python structure with the AST and use deterministic structural
  extraction for JavaScript, TypeScript, and Go.
- Keep structural and summary caches separate and detect stale summaries.
- Watch for source changes, run quick or full repository/folder summaries,
  inspect token estimates, and export context with optional manifest,
  inventory, audit, and coverage information where supported.
- Run provider-free for scanning, structural views, and source-based exports.

Summary generation sends the selected source content and configured prompt to
the explicitly selected provider. Cached summaries may be reused locally. See
[Privacy and data flow](docs/privacy-and-data-flow.md) before enabling a
provider.

## Five-minute offline demo

The example repository is synthetic and contains Python, TypeScript, Go,
Markdown, TOML, a vendored directory, a generated directory, invalid Python
syntax, and a binary-file generator. The binary is created only when requested;
no binary fixture is committed.

1. Install MergeMonster using the [installation steps](#installation).
2. Generate the demo’s tiny binary fixture:

   ```bash
   python examples/synthetic-repository/make_binary_fixture.py
   ```

3. Launch the desktop app with `mergemonster` and open
   `examples/synthetic-repository`.
4. Scan the folder. Confirm that generated and vendored paths are filtered or
   excluded by the active settings, and that the binary is not treated as
   source.
5. Set `src/service.py` to **Skeleton**, `src/client.ts` to **Code**,
   `docs/overview.md` to **Code**, and `src/settings.toml` to **Exclude**.
6. Inspect the Python structural preview. The invalid Python file should show
   an explicit unavailable/error state rather than silently becoming full
   source in skeleton mode.
7. To demonstrate summary flow, configure Groq as described in
   [Provider configuration](docs/provider-configuration.md), choose a short
   summary for one synthetic source file, and approve its preflight. This step
   is optional and sends that synthetic source to the configured provider.
   Without credentials, continue with code and skeleton modes.
8. Review the token preview and create a context output using the UI’s merge
   action. If you skipped the optional provider step, this code-and-skeleton
   flow needs no provider account.
9. Inspect the output’s file headings and, if selected, its JSON manifest.
10. Change a function signature in `src/service.py` and rescan or refresh the
   structural view. The view’s source fingerprint changes; the summary cache
   freshness mechanism is separate.

This walkthrough describes the existing desktop workflow. It does not claim
that a live provider request has been tested. The synthetic tree’s optional
`ai/MAP.md` and `ai/manifest.yml` support the separate subsystem context-pack
builder; they are not required for ordinary scanning or merging.

For an automated terminal walkthrough through the installed local API, use
`examples/run_offline_demo.py`. It creates and removes a disposable copy, scans
it, assigns mixed modes, previews and writes an export, then edits a source and
checks that the rendered skeleton changes. See the [demo output](docs/demo.md):

```bash
python examples/run_offline_demo.py --scratch "$DEMO_SCRATCH"
```

Set `DEMO_SCRATCH` to an existing temporary-data directory with enough free
space. The demo removes its temporary repository and app state when it exits.

## Installation

The `mergemonster` command opens the native desktop window. To install from
source, clone the repository and create a virtual environment.

```bash
git clone https://github.com/velhud/mergemonster-oss.git
cd mergemonster-oss
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[desktop]'
mergemonster
```

The `desktop` extra supplies the native webview integration. The core package
can also be installed with `python -m pip install .` for headless server use.
Provider support is optional; install it with
`python -m pip install '.[desktop,providers]'` only when you intend to generate
summaries through Groq. The development path uses
`python -m pip install -e '.[dev,desktop]'`. Check [Provider configuration](docs/provider-configuration.md)
for the selection and credential contract.

## File modes

| Mode | Exported representation | Provider required |
| --- | --- | --- |
| Exclude | Nothing | No |
| Code | Full readable source | No |
| Skeleton | Local structural view | No |
| Summary (short or long) | Cached or newly generated summary | New summary requires a provider |
| Code + summary | Summary and full source | New summary requires a provider |
| Summary + skeleton | Summary and local structure | New summary requires a provider |
| Code + skeleton | Full source and local structure | No |

The UI exposes additional supported combinations. Exact mode names and output
ordering are documented in [Context formats](docs/context-formats.md).

## Example output

A merged context file is plain text with a file tree and delimited file blocks.
For example, the synthetic demo may produce a shape like this (contents depend
on the selected modes):

  ```text
  ======= FILE TREE =======
  docs/overview.md
  src/client.ts
  src/service.py
  ======= END OF FILE TREE =======

  ======= START OF FILE: src/service.py =======
  # SKELETON
  # language: python
  # parser_status: ok
  from .settings import ServiceSettings
  class Service:
      def health(self) -> dict[str, str]: ...
  ======= END OF FILE: src/service.py =======

  ======= START OF FILE: docs/overview.md =======
  # Synthetic service
  This example has no network service or real customer data.
  ======= END OF FILE: docs/overview.md =======
  ```

The exact header format is configurable. The optional context-pack builder is a
different output path: it requires repository metadata under `ai/` and records
the selected subsystem and included files.

## Architecture

`codemerge.filemerger.scan` walks the selected root and applies extension,
binary, exclusion, size, and virtual-environment filters. The core controller
keeps scan state and per-file choices. `codemerge.structure` creates local
structural views, and the summary orchestrator manages provider-backed summary
freshness and caches. `FileProcessor.merge_with_plan` renders the chosen modes;
the estimator shares the writer’s transforms so token preview tracks exported
content. The optional JS UI server and static UI provide the desktop boundary.
See [Architecture](docs/architecture.md) for ownership and data-flow details.

## Privacy and security

Source scanning and structural extraction are local. Provider-backed calls are
the exception: summary prompts and selected source text are sent to the chosen
provider when the user invokes summary work. Local caches and generated output
remain on the machine unless the user moves or shares them. The UI/API binds to
loopback by default; it is intended for local use, not public internet exposure.
See [Privacy and data flow](docs/privacy-and-data-flow.md) and
[Security policy](SECURITY.md).

## Supported platforms and limitations

The package supports Python 3.11 and 3.12. CI exercises Ubuntu and macOS on
those versions, including offline and headless server checks. Native desktop
window behavior depends on host webview libraries and is not covered by the
headless CI job. Windows is not in the current CI matrix.

- Python structural extraction uses the standard AST and reports syntax errors.
- JavaScript, TypeScript, and Go structural views use deterministic fallback
  extraction; they are less parser-aware than Python AST extraction.
- Unsupported languages and binary files do not receive structural source
  views. Skeleton mode reports unavailable structure instead of silently
  substituting full source.
- Token counts are local estimates using the configured tokenizer. They do not
  guarantee parity with every provider’s request framing or tokenizer.
- New summaries require explicit provider configuration and network access.
- Xcode target controls require Xcode project metadata and are platform-specific.
- Subsystem context packs require `ai/MAP.md` and `ai/manifest.yml` with valid
  paths. This metadata is not needed for ordinary scans and merges.
- Extremely large files, unsupported encodings, and very large repositories may
  be skipped or constrained by configured limits.

Live provider operation remains subject to separate qualification.

## Development and verification

See [Development](docs/development.md) for environment setup and the checks
used by the repository. The deterministic checks do not require provider
credentials. Provider-backed calls are not part of default offline checks.

```bash
python -m pytest -q
python -m ruff check codemerge tests --select F821
```

## Project information

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [License](LICENSE)
- [Third-party notices](THIRD_PARTY_NOTICES.md)
