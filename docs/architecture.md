# Architecture

This page describes the current standalone application structure. The Python
package is `codemerge`. The user-facing product
and installed command are MergeMonster.

## Request and data flow

```text
Native desktop window (pywebview)
        │ loopback HTTP
        ▼
codemerge.jsui.server ── CoreController / services
        │                       │
        │                       ├─ filemerger.scan
        │                       ├─ structure.extractor
        │                       ├─ summarizer.shared / summarizer.router
        │                       └─ filemerger.api.FileProcessor
        ▼
static UI, selected workspace, local cache, explicit output path
```

The UI uses a local HTTP server for assets and application actions inside the
desktop window. That server is an implementation boundary for the app; the
normal experience is not a browser-only localhost product.

## Scanner and filters

`codemerge.filemerger.scan` traverses the selected root and creates file
metadata. It can apply extension/category handling, binary checks, excluded
files and directories, virtual-environment detection, size and depth limits,
and configured symlink behavior. `CoreController.scan` coordinates scan state
and the view’s available file modes. Inclusion diagnostics are assembled by
the diagnostics service and exposed in the UI.

Generated, vendored, dependency, and build folders are not universally
classified by their names. Their treatment depends on the configured excludes
and summary-vendoring rules. Review the scan result and diagnostic reasons before
exporting.

## Per-file modes and structural extraction

The controller and `FileMode` enum preserve per-file choices. The merge plan
maps those choices into the writer’s source, summary, skeleton, and combined
representations. `codemerge.structure.extractor.extract_file_structure` reads
files locally and caches by selected-root identity, relative path, source
SHA-256, and extractor fingerprint.

Python uses `ast` through `python_ast_adapter.py`. JavaScript, TypeScript, and
Go use deterministic line-level fallback extraction through
`tree_sitter_adapter.py`; despite that adapter’s name, these public fallback
paths do not require a Tree-sitter parser. Unsupported languages return an
explicit unsupported status. Invalid Python returns parser diagnostics. These
states do not silently switch a requested skeleton to full source.

## Summary engine, provider boundary, and cache freshness

`codemerge.summarizer.shared.orchestrator` and the quick/full summarizer modules
coordinate file and aggregate summaries. Provider calls are made through
`codemerge.llm`; summary-cache identity includes source freshness and summary
configuration such as model and prompt version. The summary cache is separate
from the structural-view cache. Cache hits can be reused offline; generating or
refreshing a summary requires the selected provider.

The current release contract selects Groq explicitly and sends requests through
its OpenAI-compatible Responses API. The provider boundary is optional. See
[Provider configuration](provider-configuration.md) for selection, credentials,
and data sent. This documentation does not claim that a live request has been
tested.

Watch mode invokes change-detection and summary orchestration for the current
workspace on an interval. It can mark stale data and refresh according to the
active configuration. Vendored summary behavior is controlled separately from
ordinary scan filtering.

## Token estimation and merge/export writer

`FileProcessor` in `codemerge.filemerger.api` owns file reads, token counting,
transforms, and `merge_with_plan`. The estimator uses the same content
transforms and mode decisions as the merge writer, including file headers,
tree, table of contents, and optional line transforms where enabled. Token
counts use the bundled `o200k_base` tokenizer. Provider request framing and
provider-side token accounting can differ.

Ordinary merge output is text written to the selected destination (or supplied
stream). The optional JSON manifest records merge information when requested.
The context-pack builder is a distinct path: `codemerge.filemerger.context_pack`
requires repository metadata files `ai/MAP.md` and `ai/manifest.yml`, selects a
declared subsystem, and can build subsystem or audit output with included-file
and dependency information.

## Background jobs and workspace sessions

The JS UI exposes bounded process-local jobs for scans, merges, summaries, and
other long-running operations. A job captures its root, config, scan context,
and mode selections before it starts; later switching the UI workspace does not
rewrite that captured input. Status is inspectable through the jobs API. Job
cancellation support varies by job type and is exposed in job metadata.

Workspace-session endpoints export, import, select, and inspect current UI
state. These are application state snapshots; they are not a general-purpose
portable backup format. Session JSON intentionally retains absolute local
recovery paths, and imported paths still pass through path policy. Review a
session export for local path disclosure before sharing it.

## Local API, UI, and filesystem safety boundary

The JS UI server is defined in `codemerge.jsui.server`; static files live under
`codemerge/jsui/static`. It binds to loopback by default. Mutating API requests
use a session token bootstrapped by the session endpoint. File reads, writes,
downloads, open requests, listings, and probes use purpose-scoped path grants
from `codemerge.jsui.path_policy`. These protections support local desktop use;
they do not make the server suitable for public network exposure.

The filesystem boundary is rooted in the selected workspace or an explicit
destination as each operation requires. Symlink and path rules are enforced by
the relevant path-policy and scan services. See [Security policy](../SECURITY.md)
for disclosure instructions and [Privacy and data flow](privacy-and-data-flow.md)
for storage and provider boundaries.
