# Privacy and data flow

MergeMonster is a local desktop application. Repository scanning, filtering,
structural extraction, token preview, and ordinary merge output operate on the
selected local files. The app does not upload a repository merely because it is
opened or scanned.

## Local data

- The selected workspace is read according to the active scan filters and
  explicit file modes.
- Merge output is written to the destination selected by the user.
- UI configuration, caches, and workspace/session state are stored locally.
  Settings are saved in `settings.json`. The preferred home directory is `~/.mergemonster`, overridable with
  `MERGEMONSTER_HOME`. `CODEMERGE_HOME` and the historical `~/.codemerge`
  location are honored for compatibility.
- Workspace-session exports intentionally retain absolute local recovery
  paths so a session can restore the same workspace on that machine. Review
  these JSON files before sharing them; they may disclose local directory names.
- Tokenization uses the bundled `o200k_base` data; ordinary first run does not
  download tokenizer data.
- Logs and diagnostics are local application output. Avoid sharing them without
  reviewing for local paths or repository names.

## Optional provider calls

Summary and AI-compare actions may transmit selected source material. The
current provider contract is Groq via its OpenAI-compatible Responses API.
Before sending, MergeMonster’s provider preflight identifies the operation and
its estimated scope. The user must invoke the provider-backed action and
configure a supported model in Settings.

For summary work, sent content may include the selected file text or selected
chunks, the summarization prompt, and the context assembled for the chosen
summary operation. Aggregate summaries may include summaries of files in the
selected repository/folder scope. Excluded files and binary content are not
intended as summary input. Review the operation scope before confirmation.

Credentials are read from the process environment variable `GROQ_API_KEY`.
MergeMonster does not discover `.env` files and does not fall back to
`OPENAI_API_KEY`. Do not put a real key in an example config, repository, issue,
or log. Provider calls leave the machine and are subject to the provider’s own
retention, privacy, and account terms.

The source implementation supports additional provider-specific retrieval or
embedding paths separately from summaries. Their availability depends on the
release’s installed extras and explicit configuration; they are not required
for the offline first run.

## Generated files and sharing

Context output can contain the exact source, summaries, structural views, file
paths relative to the selected root, and optional manifest data chosen for the
export. Treat it as sensitive to the same degree as the selected source.
MergeMonster does not publish or upload that output automatically. The user is
responsible for the selected output location and any later sharing.

The local server is an internal desktop boundary and binds to loopback by
default. It is not designed for public internet access. See
[Architecture](architecture.md) and the root `SECURITY.md` for more detail.
