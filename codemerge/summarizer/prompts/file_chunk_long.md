SCOPE
- Job: FILE_CHUNK_EXTRACT_LONG
- Profile: {{PROFILE}}                 # explore | merge
- Target kind: file_chunk
- Repo name: {{REPO_NAME}}
- File path (rel): {{FILE_REL}}
- Chunk index: {{CHUNK_INDEX}} / {{CHUNK_TOTAL}}
- Chunk contains: {{CHUNK_HINT}}        # e.g., "top of file", "middle", "end", "unknown"

FILE CHUNK CONTENT (no truncation of this chunk)
{{FILE_CHUNK_CONTENT}}

TASK
Extract concrete, mergeable facts from THIS chunk only. Do not guess what is outside the chunk.

OUTPUT OUTLINE (follow this order)
# Chunk extract: `{{FILE_REL}}` ({{CHUNK_INDEX}}/{{CHUNK_TOTAL}})
## Observed responsibilities in this chunk
- What this chunk appears to implement (bullets).

## Symbols defined or used
- Functions/classes/constants defined here (names only).
- Notable symbols referenced/called (names only) if meaningful.

## Interfaces / entrypoints visible here
- Any handlers/routes/CLI hooks/exports/config entrypoints visible in this chunk.

## Dependencies visible here
- Imports used here (as written).
- External services hinted here (URLs, client names).

## Side effects visible here
- DB / Network / FS / Auth / Process — only what is visible in the chunk.

## Strong “where to change” hooks
- Pointers like: “The logic for X is in function Y” (only if present).

## Uncertainties
- What you cannot know from this chunk.
