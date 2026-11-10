SCOPE
- Job: FILE_SUMMARY_LONG_FROM_CONTENT
- Profile: {{PROFILE}}                 # explore | merge
- Target kind: file
- Repo name: {{REPO_NAME}}
- File path (rel): {{FILE_REL}}
- File path (abs): {{FILE_ABS}}
- Language hint (extension): {{FILE_EXT}}
- File size bytes: {{FILE_SIZE_BYTES}}

FILE CONTENT (full, no truncation)
{{FILE_CONTENT}}

OUTPUT OUTLINE (follow this order)
# `{{FILE_REL}}`
## Responsibility
- What this file is for in the system (1–5 bullets, concrete).

## Key entry points / exports
- List the main exported functions/classes/handlers/CLI commands/routes/config entrypoints seen in this file.
- If none, say “None obvious”.

## How it works (high-level)
- 3–10 bullets describing the main logic flow(s) in this file (only what is visible here).

## Important dependencies
- Internal imports (by module/import path as written).
- External libraries/services (by package name).
- Mention only what you can see.

## Side effects
Cover these explicitly when present:
- DB
- Network/API calls
- Filesystem
- Auth/permissions
- Process/runtime (subprocess, env vars, signals, threads, jobs)

## Where to change X (practical pointers)
- 5–12 bullets: “To change <thing>, edit <function/section> in this file” (and name the symbol or region).
- Do NOT point to other files unless this file explicitly references them by path/import.

## Uncertainties
- List anything that cannot be concluded from this file alone (entrypoints, callers, runtime wiring).
