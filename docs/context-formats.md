# Context formats

MergeMonster has two related but distinct output paths.

## Merged context file

The ordinary merge accepts a per-file plan. The `FileMode` values include:

| Mode | Content |
| --- | --- |
| `EXCLUDE` | No content for the file |
| `CODE` | Readable source |
| `SKELETON` | Local structural view |
| `API_SURFACE`, `SIGNATURES` | Structural views with the corresponding requested presentation |
| `CODE_PLUS_SKELETON` | Structural view and source |
| `SUMMARY_SHORT`, `SUMMARY_LONG` | Selected cached or newly generated summary |
| `CODE_PLUS_SHORT`, `CODE_PLUS_LONG` | Summary and source |
| `SUMMARY_SHORT_PLUS_SKELETON`, `SUMMARY_LONG_PLUS_SKELETON` | Summary and structural view |
| `CODE_PLUS_SUMMARY_SHORT_PLUS_SKELETON`, `CODE_PLUS_SUMMARY_LONG_PLUS_SKELETON` | Summary, structural view, and source |

The writer may add a file tree, file delimiters, optional Markdown fences or
table of contents, and configured text transforms. These options are rendered
by `FileProcessor.merge_with_plan`; the token estimator follows the same mode
and transform decisions. A JSON merge manifest is optional.

Summary modes can use cached summaries without a new provider request. Missing
or stale summaries may trigger provider-backed work according to the active
configuration. Provider-free source, skeleton, and exclude modes remain
available offline.

## Subsystem context pack

`codemerge.filemerger.context_pack.build_pack` creates a deterministic pack for
a named subsystem in a repository. It requires:

- `ai/MAP.md`, describing repository structure;
- `ai/manifest.yml`, declaring subsystem roots, shared files, and public API
  files.

An optional `docs/CONTRACT.md` and declared contract tests may also be included.
The `subsystem` mode collects the declared files. The `audit` mode additionally
analyzes Python module dependencies and reports unresolved dependencies. It
applies size, extension, excluded-path, and virtual-environment filters and
returns included paths and warnings. This specialized format is not required
for ordinary repository scans or merges.

The synthetic example includes small metadata files demonstrating the required
shape. Adapt the paths and subsystem definitions to your own repository; do not
copy its example manifest as a claim of completeness for another project.
