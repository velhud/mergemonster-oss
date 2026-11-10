# Changelog

Notable changes for MergeMonster are recorded here.

## 0.1.0

- Introduces a standalone MergeMonster package with a `mergemonster` command
  for the native desktop experience and an explicit loopback server mode.
- Exposes repository scanning, per-file content modes, local structural views,
  summary caching, token preview, and merge/context output.
- Includes local path policy, session-token protection for mutating local API
  actions, bounded inspectable jobs, and workspace-session endpoints.
- Adds a synthetic multilingual example repository and public documentation for
  architecture, provider setup, privacy, context formats, development, and
  troubleshooting.
- Provider-backed summaries remain optional and require explicit Groq model
  selection and `GROQ_API_KEY` configuration.

See the release notes and CI results for verified platform and Python support.
