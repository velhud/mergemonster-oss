SCOPE
- Job: REPO_SUMMARY_FULL
- Profile: {{PROFILE}}                         # explore | merge
- Target kind: repo
- Repo name: {{REPO_NAME}}
- Root path (rel): /
- Manifest id: {{MANIFEST_ID}}
- Scope label: {{SCOPE_LABEL}} (v{{SCOPE_DEF_VERSION}})
- Coverage intent: Full is coverage-preserving bottom-up; no sampling.

INVENTORY (authoritative: top-level folders + root files)
{{INVENTORY_LIST}}

TREE SNAPSHOT (names only)
{{TREE_SNAPSHOT}}

ANCHOR FILES (optional)
{{ANCHOR_BLOCKS}}

EVIDENCE ITEMS (ALL top-level folder Full summaries + key root file summaries, reduced if needed)
{{EVIDENCE_BLOCKS}}

TASK
Produce an architect-level repo map:
- Major subsystems and responsibilities
- How it runs / entry points
- Cross-subsystem flows
- Dependencies / infrastructure
- Side effects map
- Practical “where to change X” pointers
- Every INVENTORY item must appear at least once in the major subsystems section and/or Where to change X.
- Use the OUTPUT OUTLINE headings exactly as written below. Do not rename headings or add extra path/title lines.
- When mentioning internal code locations, use exact INVENTORY paths or obvious subtree roots only. Do not invent dotted internal module names.
- When mentioning internal code locations, use exact INVENTORY paths or obvious subtree roots only. Do not invent dotted internal module names, member calls, or class-style tokens such as `api.merge()`, `selector.select()`, or `config.AppConfig`.
- In `Dependencies / infrastructure`, keep external dependencies generic by product or package name and keep internal infrastructure tied to exact INVENTORY paths only. Do not enumerate dotted module names or other non-inventory tokens in this section.
- If the evidence only suggests an external module or helper that is not an exact INVENTORY path, move that reference to `Uncertainties` instead of `Dependencies / infrastructure`.
- Keep repo-relative child paths fully qualified with their top-level inventory prefix. If a path lives under `codemerge/`, write `codemerge/...` rather than shortening it to `core/...`, `prune/...`, or `summarizer/...`.
- When evidence exposes a concrete runtime entrypoint, CLI, server, router, startup file, or orchestrator below a top-level folder, surface that exact repo-relative child path in `Entry points / how it runs` and `Where to change X`.
- When evidence exposes a concrete service-layer change point or config file below a top-level folder, carry that exact child path into `Where to change X` instead of only naming the broader subsystem.
- When root files are in scope, mention them by exact root filename rather than inventing nested paths or omitting them.
- When the evidence exposes a concrete repo-relative child path such as a server, router, CLI, or startup file below a top-level folder, preserve that concrete child path instead of collapsing it back to only the parent folder.
- When the evidence exposes a concrete service-layer change point such as `merge_service.py`, `router.py`, `server.py`, `config.py`, or an orchestrator, carry that exact child path into `Where to change X` instead of only naming the broader subsystem.
- When naming code files, keep the `.py` suffix. Do not shorten `codemerge/models.py` to `codemerge/models`, `codemerge/constants.py` to `codemerge/constants`, or similar bare stems.
- Ignore README or documentation-only commands when their referenced path is absent from MANIFEST. If documentation mentions a path that is not in scope, treat it as an uncertainty instead of a concrete runnable path.
- When docs mention placeholder paths, module-run commands, or CLI command names, rewrite them in plain English instead of preserving the literal token.
- If docs mention a config path absent from the inventory, describe it as the runtime config file in plain English.
- When a provider or model identifier contains slashes such as `openai/gpt-oss-20b`, rewrite it as prose instead of preserving the slash token.
- In `Side effects map`, use explicit words such as `database`, `sqlite`, `filesystem`, `network`, `auth`, `config`, and `process`; avoid abbreviations like `DB` or `FS`.
- In `Side effects map`, include explicit `sqlite` and `filesystem` bullets whenever caching or file I/O are part of the evidence. Do not collapse them into a generic `database` or `I/O` line.
- When discussing cache freshness or change detection, spell it out as separate terms such as `mtime`, `size`, `sha256`, and `git`; do not compress them into slash-delimited shorthand or invented composite tokens.
- When describing UI actions or workflow labels, do not compress them into slash-delimited shorthand such as `preview/copy/download` or `scan/merge/summarise`; use ordinary prose or comma-separated wording unless the token is a real repo path.
- When listing provider names or external services, do not compress them into slash-delimited shorthand; use commas or `and`.
- When citing exact filenames, keep the full filename including suffix rather than dropping the extension.
- When listing multiple valid paths, never join them with `/` or other slash-separated alternatives. Use separate bullets or comma-separated paths instead.
- Be concise and avoid repetition: do not repeat the same path, subsystem, or evidence list across multiple sections. Cover each concrete path once in the most relevant section, and only mention it again if the later sentence adds new information.
- If unclear, state uncertainty; do not invent paths.

OUTPUT OUTLINE
# Repository: `{{REPO_NAME}}`
## System overview
- 5–15 bullets.

## Major subsystems
- Cluster into 5–15 subsystems. For each:
  - Role (bullets)
  - Representative top-level paths (from INVENTORY and/or obvious subtree roots)

## Entry points / how it runs
- Bullets: startup scripts, main services, CLIs, schedulers, tests harnesses, deployment entrypoints (only if supported by evidence).
- Prefer the most concrete evidence-backed path when available, not just the parent folder.

## Cross-subsystem flows
- 5–15 bullets: request→core→persistence, UI→API, worker pipelines, etc (only if inferable).

## Dependencies / infrastructure
- External services, databases, queues, cloud SDKs, etc. by product or package name, not path-like tokens.
- Internal infrastructure areas by exact INVENTORY path when naming paths.
- Do not list dotted module names or other internal-looking non-inventory references here. If the evidence mentions them, treat them as uncertainties and describe them in `Uncertainties` instead.

## Side effects map
- Name `database` / `sqlite`, `filesystem`, `network`, `auth`, `config`, and `process` zones where they mostly live (paths/subsystems).

## Where to change X
- 12–30 bullets. Format:
  - “To change <goal>, start at <path(s)>; then check <related area>.”
- Keep concrete valid child paths when evidence supports them; do not flatten them to broader parent folders.
- Cover the highest-signal change points from each major subsystem; for merge-related areas, prefer service-layer runtime paths when they are present in evidence.

## Uncertainties
- Bullets: unclear entrypoints, suspicious evidence mentions not in inventory, unknown deployment wiring.
- Include any inferred external modules or helpers here when the evidence mentions them but they are not exact INVENTORY paths.

## Coverage note
- Manifest: `{{MANIFEST_ID}}`
- Scope: `{{SCOPE_LABEL}}` (v{{SCOPE_DEF_VERSION}})
- Note: This summary is derived from complete bottom-up coverage of in-scope content.
