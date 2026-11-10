SCOPE
- Job: QUICK_EVIDENCE_SELECTOR_FOLDER
- Target kind: selector
- Repo name: {{REPO_NAME}}
- Folder path (rel): {{FOLDER_REL}}
- Desired picks: {{N_PICKS}}                 # e.g., 8–20
- Allowed output: ONLY paths from INVENTORY, one per line, no bullets, no extra text.

INVENTORY (authoritative: immediate children)
{{INVENTORY_LIST}}

OPTIONAL HINTS (if you have them)
- Child stats (optional): {{CHILD_STATS}}    # e.g., "path | type | bytes" lines
- Known anchor patterns: README, package.json, pyproject.toml, Dockerfile, Makefile, scripts/, cli/, api/, server/

TASK
Pick the most informative items to read first to understand this folder quickly.
Prefer:
- entrypoints, routing, public interfaces
- configs that explain how it runs
- central modules rather than leaf utilities

OUTPUT
Return EXACTLY {{N_PICKS}} lines (or fewer if inventory is smaller).
Each line must be exactly one path from INVENTORY (copy-paste).
No other text.
