# Security policy

Report suspected vulnerabilities through GitHub's private vulnerability reporting when available. Include the MergeMonster version, a synthetic reproduction, and the affected operation. Keep credentials, source from real repositories, and exploit details out of public issues.

## Trust boundaries

MergeMonster runs as the signed-in desktop user. Its HTTP server listens on loopback, validates the request host and browser origin, and requires a per-process session token for mutating API calls. Treat other processes running as the same OS user as trusted. Use the desktop app or the loopback development server on your own machine.

The desktop launcher enables JavaScript evaluation for pywebview's native bridge. Both modes restrict script sources to the local origin and reject inline scripts. Headless mode also blocks string evaluation.

A repository scan is scoped to the selected workspace. Symlinks that resolve outside it are excluded. File and folder grants for comparisons, browsing, output and OS-open operations are explicit local choices. Downloads require a token. Choose a separate export destination if you want to protect source files from accidental replacement.

A context export identifies files by relative paths. Workspace recovery sessions and local logs can contain absolute paths, selections and settings. Inspect those files before sharing them. Configuration, caches and logs live in the MergeMonster state directory.

Provider requests are optional and require explicit confirmation in the UI. The configured provider can receive selected source text, relative paths, repository names, and derived summaries, depending on the action. Review the provider preflight and [privacy documentation](docs/privacy-and-data-flow.md) before confirming. Use only trusted repositories for optional execution-based prune verification, which runs with your local user privileges.
