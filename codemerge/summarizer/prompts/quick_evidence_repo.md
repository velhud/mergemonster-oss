SCOPE
- Job: QUICK_EVIDENCE_SELECTOR_REPO
- Target kind: selector
- Repo name: {{REPO_NAME}}
- Desired picks: {{N_PICKS}}
- Allowed output: ONLY paths from INVENTORY, one per line, no extra text.

INVENTORY (authoritative: top-level folders + root files)
{{INVENTORY_LIST}}

OPTIONAL HINTS
{{CHILD_STATS}}

TASK
Pick the most informative top-level areas/files to inspect first to understand the repo quickly.
Prefer:
- entrypoints/run wiring (root scripts, config)
- main subsystems (api/, server/, src/, app/, services/, etc.)
- docs explaining architecture

OUTPUT
Return EXACTLY {{N_PICKS}} lines (or fewer if inventory is smaller).
Each line must be exactly one path from INVENTORY.
No other text.
