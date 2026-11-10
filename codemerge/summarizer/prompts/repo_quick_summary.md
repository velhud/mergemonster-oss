SCOPE
- Job: REPO_SUMMARY_QUICK
- Profile: {{PROFILE}}                       # explore | merge
- Target kind: repo
- Repo name: {{REPO_NAME}}
- Root path (rel): /
- Note: Quick is orientation-grade and may be selective.

INVENTORY (authoritative: top-level folders + root files)
{{INVENTORY_LIST}}

TREE SNAPSHOT (names only)
{{TREE_SNAPSHOT}}

ANCHOR FILES (optional)
{{ANCHOR_BLOCKS}}

EVIDENCE ITEMS (selected top-level folder summaries and/or key file summaries)
{{EVIDENCE_BLOCKS}}

TASK
Produce a quick repo map that tells an engineer/agent:
- What this repo is.
- Where to start.
- Major areas.
- Likely entrypoints/how it runs (or say unclear).
- Where to change common behavior first.
- Likely side effects / state zones (only if supported by evidence).
- When selected folder summaries expose concrete child entrypoints such as startup files, servers, routers, or CLIs, prefer naming those child paths explicitly instead of only naming the parent folder.
- If INVENTORY is modest, try to cover each materially different top-level area at least once across the output.
- Use explicit side-effect words such as `database`, `sqlite`, `filesystem`, `network`, `config`, and `process`; avoid abbreviations like `DB` or `FS`.
- When discussing cache freshness or change detection, spell it out as separate terms such as `mtime`, `size`, `sha256`, and `git`; do not compress them into slash-delimited shorthand or invented composite tokens.
- When describing UI actions or workflow labels, avoid slash-delimited shorthand that looks like a path; use ordinary prose or comma-separated wording unless the token is a real repo path.
- Prefer exact INVENTORY items. You may also mention evidence-backed repo-relative child paths when they are clearly supported by selected folder summaries or anchor files.
- Keep repo-relative child paths fully qualified with their top-level inventory prefix. Do not shorten `codemerge/...` paths to subtree-only forms like `core/...` or `summarizer/...`.
- When a selected folder summary exposes a concrete runtime or server entrypoint below that folder, prefer naming that repo-relative child path explicitly instead of only the parent folder.
- If selected evidence exposes concrete child paths inside `filemerger/`, `jsui/`, or `summarizer/`, carry those exact repo-relative paths upward instead of collapsing back to the parent folder.
- In `Where to change behavior first`, every bullet must start with `To change` and must contain a concrete path.
- If selected evidence exposes `server.py`, `router.py`, `merge_service.py`, `config.py`, or `orchestrator.py`, carry at least one of those concrete paths into either `Where to start`, `Likely entry points / how it runs`, or `Where to change behavior first`.
- If selected evidence exposes runtime wiring, UI routes, provider setup, config files, or service-layer change points, surface the most concrete evidence-backed path instead of only broad subsystem names.
- Be concise and avoid repetition: do not repeat the same path or evidence list across multiple bullets. Each concrete path should appear once in the most relevant bullet unless a later sentence adds new information.
- Do not repeat the same path across long runs of bullets. If a path already establishes a point, move to the next concrete area instead of restating it.
- Do not surface machine-local absolute paths, cache file locations, or user-home paths as primary repo guidance.
- Do not present paths as active code if they are not inside the current INVENTORY or selected evidence scope. README or config files may mention excluded or out-of-scope areas; ignore those paths unless they also appear in scope.

OUTPUT OUTLINE
# Repository: `{{REPO_NAME}}`
## What this repo is
- 3–8 bullets.

## Where to start
- 5–12 bullets pointing to concrete paths and why.
- If evidence supports a concrete repo-relative child path such as `folder/server.py` or `folder/main.py`, prefer that concrete path over only naming the parent folder.

## Major areas (top-level)
- 5–20 bullets, grouped by subsystem.

## Likely entry points / how it runs
- Bullets: main scripts, server start, CLI, workflow runners, etc (only if supported).
- Prefer the most concrete repo-relative runtime path supported by evidence, including child paths surfaced by selected folder summaries.

## Where to change behavior first
- 4–10 bullets.
- Every bullet must start with `To change`.
- Format exactly: “To change <goal>, start at <path(s)> because <reason>.”
- Prefer exact INVENTORY items, but evidence-backed repo-relative child paths are allowed.

## Likely side effects / state
- Bullets. Mention `database` / `sqlite`, `filesystem`, `network`, `config`, or `process` only if supported. If unclear, say “Unclear from current evidence”.

## Uncertainties
- Bullets.
