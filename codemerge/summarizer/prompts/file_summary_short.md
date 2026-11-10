SCOPE
- Job: FILE_SUMMARY_SHORT_FROM_CONTENT
- Profile: {{PROFILE}}                 # explore | merge
- Target kind: file
- Repo name: {{REPO_NAME}}
- File path (rel): {{FILE_REL}}
- File path (abs): {{FILE_ABS}}
- Language hint (extension): {{FILE_EXT}}

FILE CONTENT (full, no truncation)
{{FILE_CONTENT}}

OUTPUT OUTLINE (follow this order)
# `{{FILE_REL}}`
## Responsibility
- 1–3 bullets.

## Entry points / exports (if any)
- 1–8 bullets.

## Side effects (if any)
- DB / Network / FS / Auth / Process: mention only those present.

## Where to change things
- 3–6 bullets pointing to symbols/sections in THIS file.

## Uncertainties
- 1–5 bullets.
