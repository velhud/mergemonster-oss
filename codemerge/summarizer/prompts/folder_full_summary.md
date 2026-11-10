SCOPE
- Job: FOLDER_SUMMARY_FULL
- Profile: {{PROFILE}}                         # explore | merge
- Target kind: folder
- Repo name: {{REPO_NAME}}
- Folder path (rel): {{FOLDER_REL}}
- Manifest id: {{MANIFEST_ID}}
- Scope label: {{SCOPE_LABEL}} (v{{SCOPE_DEF_VERSION}})
- Coverage intent: Full is coverage-preserving bottom-up; no sampling.

INVENTORY (authoritative: immediate children of this folder)
{{INVENTORY_LIST}}

TREE SNAPSHOT (names only)
{{TREE_SNAPSHOT}}

ANCHOR FILES (optional)
{{ANCHOR_BLOCKS}}

EVIDENCE ITEMS (ALL immediate child items, either direct or reduced via group summaries)
{{EVIDENCE_BLOCKS}}

TASK
Produce an architect-level module map for this folder:
- Explain role, internal structure (by responsibility buckets), interfaces, flows, dependencies, side effects zones.
- Give practical “where to change X” pointers using exact INVENTORY items and/or repo-relative paths rooted at `{{FOLDER_REL}}/`.
- If the evidence shows a concrete runtime or CLI entrypoint inside this folder, list that exact path explicitly in `Interfaces / entry points`.
- If the evidence shows a concrete configuration, manifest, cache, or orchestration file, surface that file as its own change point instead of hiding it inside a broad bucket.
- If the evidence shows browser UI, HTTP routes, background jobs, or provider wiring, make those responsibilities explicit in `Structure map` and `Where to change X` rather than burying them in generic wording.
- When the evidence mentions UI actions or export affordances, spell them out in prose or comma-separated wording; do not compress them into slash-delimited shorthand such as `preview/copy/download`.
- Every INVENTORY item must appear at least once in the Structure map and/or Where to change X.
- Use the OUTPUT OUTLINE headings exactly as written below. Do not rename headings, add parenthetical suffixes, or add a path-specific title line.
- Keep internal references constrained to exact INVENTORY items and/or repo-relative paths rooted at `{{FOLDER_REL}}/`. Do not introduce dotted Python module names unless they are also valid inventory-backed paths.
- When the evidence exposes a concrete repo-relative child path such as `{{FOLDER_REL}}/server.py` or `{{FOLDER_REL}}/subdir/router.py`, preserve that concrete child path instead of flattening it to only the parent folder.
- When evidence exposes a concrete service-layer change point such as `merge_service.py`, `router.py`, `server.py`, `config.py`, or an orchestrator below this folder, carry that exact child path into `Where to change X`.
- In `Dependencies`, keep external dependencies generic by product or package name and keep internal dependencies tied to exact INVENTORY items only. Do not enumerate dotted module names, class names, member calls, or function names in this section. If a helper is outside this folder, rewrite it in plain English or move it to `Uncertainties`.
- In `Side effects zones`, use explicit words such as `database`, `sqlite`, `filesystem`, `network`, `auth`, and `process`; avoid abbreviations like `DB` or `FS`.
- When the folder touches UI state, server routes, or persisted settings, name `config` explicitly in `Side effects zones` if the evidence supports it.
- If something is unclear, state it explicitly; do not invent.

OUTPUT OUTLINE
# Folder summary
## Role in the system
- 3–10 bullets.

## Structure map
- Cluster into 3–12 buckets. For each bucket:
  - What it does (bullets).
  - Representative paths from INVENTORY or repo-relative paths rooted at `{{FOLDER_REL}}/` (5–20 max).

## Interfaces / entry points
- Bullets: endpoints/handlers/CLIs/public APIs/config entrypoints (only if supported).
- Include module-level or CLI entrypoints such as `summarize.py` explicitly when they exist in INVENTORY.
- Prefer the most concrete evidence-backed repo-relative path when available.

## Key flows through this area
- 3–12 bullets describing how work moves through this folder (only if inferable from evidence).

## Dependencies
- Internal dependencies (areas described in plain words; when naming paths, use exact INVENTORY items or repo-relative paths rooted at `{{FOLDER_REL}}/`).
- External dependencies (libraries/services by product or package name, not path-like tokens).

## Side effects zones
- Name `database` / `sqlite`, `filesystem`, `network`, `auth`, and `process` zones where they occur (paths/buckets).
- Name `config` explicitly when the folder loads, rewrites, or filters runtime settings.
- If this area scans files, normalizes paths, watches directories, reads or writes manifests/caches, or filters in-scope files, call that out explicitly as Filesystem.

## Where to change X
- 8–20 bullets. Format:
  - “To change <goal>, look at <path(s)> and <why>.”
- Only reference exact INVENTORY items or repo-relative paths rooted at `{{FOLDER_REL}}/`.
- Keep concrete valid child paths when evidence supports them; do not flatten them to broader parent folders.
- Cover the highest-signal change points from each major responsibility bucket; for merge/runtime/config behavior, prefer the concrete service-layer path when present.
- Call out concrete change topics for runtime wiring, UI state, background work, provider setup, caches, or config files whenever the evidence supports them.

## Uncertainties
- Bullets: unclear wiring, missing entrypoints, suspicious evidence mentions not in inventory.

## Coverage note
- Manifest: `{{MANIFEST_ID}}`
- Scope: `{{SCOPE_LABEL}}` (v{{SCOPE_DEF_VERSION}})
- Note: This summary is derived from complete bottom-up coverage of in-scope children for this folder.
