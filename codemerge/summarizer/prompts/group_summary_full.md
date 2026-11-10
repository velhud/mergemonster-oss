SCOPE
- Job: GROUP_REDUCTION_SUMMARY
- Profile: {{PROFILE}}                     # explore | merge
- Target kind: group
- Repo name: {{REPO_NAME}}
- Target scope (rel): {{SCOPE_REL}}        # folder rel or "/" for repo-level grouping
- Reduction layer: {{LAYER_INDEX}}         # 1..N (optional)
- Group id: {{GROUP_ID}}                   # optional

INVENTORY (authoritative)
{{INVENTORY_LIST_FOR_SCOPE}}               # may be entire scope or immediate children list

EVIDENCE ITEMS (each item is a child file/folder/group summary)
{{EVIDENCE_BLOCKS}}

TASK
Create a compressed, mergeable group summary that preserves navigation signal.
- Cluster the items into a small number of responsibility “buckets”.
- For each bucket: explain role + interfaces + side effects + representative paths.
- Do NOT rewrite every child as prose. Compress aggressively.
- Do NOT invent paths or modules; only reference paths from INVENTORY.
- Preserve concrete evidence-backed child paths when they matter for navigation; do not flatten them into a broader parent folder unless the evidence is already that coarse.

OUTPUT OUTLINE (follow this order)
# Group summary: `{{SCOPE_REL}}` (layer {{LAYER_INDEX}} / group {{GROUP_ID}})
## Buckets
For each bucket:
### Bucket: <name>
- What it covers (1–4 bullets).
- Representative paths (5–15 max, all from INVENTORY; use backticks).
- Interfaces / entry points seen across these items (bullets).
- Side effects zones (bullets: DB/network/fs/auth/process) if present.

## Cross-bucket notes
- How these buckets relate (shared deps, shared entrypoints, flow handoffs).

## Uncertainties
- Any missing/unclear wiring or suspicious path mentions from evidence (especially evidence mentions not in inventory).
