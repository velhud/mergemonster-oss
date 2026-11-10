SCOPE
- Job: FOLDER_SUMMARY_QUICK
- Profile: {{PROFILE}}                       # explore | merge
- Target kind: folder
- Repo name: {{REPO_NAME}}
- Folder path (rel): {{FOLDER_REL}}
- Note: Quick is orientation-grade and may be selective.

INVENTORY (authoritative: immediate children of this folder)
{{INVENTORY_LIST}}

TREE SNAPSHOT (names only)
{{TREE_SNAPSHOT}}

ANCHOR FILES (optional)
{{ANCHOR_BLOCKS}}

EVIDENCE ITEMS (selected child summaries; may be partial)
{{EVIDENCE_BLOCKS}}

TASK
Produce a quick orientation map:
- What this folder seems to be for.
- What to look at first.
- Likely entrypoints/interfaces (or say unclear).
- Where to change common behavior first.
- Where side effects probably live (only if supported by evidence).
- If INVENTORY is modest, try to cover each materially different child or responsibility at least once across the output.
- Use explicit side-effect words such as `database`, `sqlite`, `filesystem`, `network`, `config`, and `process`; avoid abbreviations like `DB` or `FS`.
- Prefer exact INVENTORY items. You may also mention repo-relative paths rooted at `{{FOLDER_REL}}/` when they are clearly supported by the selected evidence.
- When selected child folder summaries expose concrete child paths such as `{{FOLDER_REL}}/server.py` or `{{FOLDER_REL}}/subdir/server.py`, prefer naming those concrete paths instead of only the parent folder.
- When `{{FOLDER_REL}}` is not the repo root, prefer rooted repo-relative paths such as `{{FOLDER_REL}}/api.py` over bare names such as `api.py`.
- If this folder itself is the root scope, write `main.py` or `services/api/router.py`; never emit `//main.py`.
- Rewrite dotted import/module references such as `services.api.router` as slash paths with extensions when the evidence makes that path clear.
- If a selected file or child summary clearly owns runtime wiring (`main.py`, `server.py`, `router.py`, `api.py`, `merge_service.py`), carry that exact path into `Likely entry points / interfaces` or `Where to change behavior first`.
- If a selected file clearly owns configuration defaults or persisted settings (for example `config.py`), include one `To change ...` bullet that names that exact config path.
- If a selected file clearly owns manifest, cache, or scope bookkeeping, include that exact path explicitly instead of collapsing it into a generic shared/cache bucket.
- When evidence mentions UI actions or export affordances, spell them out in prose or comma-separated wording; do not compress them into slash-delimited shorthand such as `preview/copy/download`.
- Do not present paths as active code if they are not inside the current INVENTORY or selected evidence scope, even if an anchor file or README mentions them.
- In `Where to change behavior first`, every bullet must start with `To change` and must contain a concrete path.

OUTPUT OUTLINE
# `{{FOLDER_REL}}/`
## What this folder seems to be
- 2–6 bullets, grounded.

## What to look at first
- 3–10 bullets: specific paths from INVENTORY + why they matter.

## Notable areas / files
- 5–20 bullets: group by theme if helpful.

## Likely entry points / interfaces
- Bullets. If unclear, say “Unclear from current evidence”.
- Prefer the most concrete repo-relative path supported by evidence, not just the parent folder name.

## Where to change behavior first
- 3–8 bullets.
- Every bullet must start with `To change`.
- Format exactly: “To change <goal>, start at <path(s)> because <reason>.”
- Only reference exact INVENTORY items or repo-relative paths rooted at `{{FOLDER_REL}}/`.

## Likely side effects / state
- Bullets. Mention `database` / `sqlite`, `filesystem`, `network`, `config`, or `process` only if supported by evidence. If unclear, say “Unclear from current evidence”.

## Uncertainties
- Bullets.
