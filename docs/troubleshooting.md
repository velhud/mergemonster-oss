# Troubleshooting

| Symptom | Check |
| --- | --- |
| Desktop window does not open | Confirm the desktop extra and host webview runtime are installed, then run `mergemonster` from the activated environment and inspect the local terminal output. |
| Tokenizer initialization fails | Reinstall the package and verify its bundled `o200k_base` tokenizer data is present. A normal first run should not need a tokenizer download. |
| Summary action asks for provider setup | Set `GROQ_API_KEY` in the launching process environment, select a supported `groq_summary_model` in Settings, and retry after reviewing preflight. |
| Summary provider/model error | Confirm the selected model is supported by Groq and the optional provider dependency is installed. MergeMonster does not fall back to another provider automatically. |
| File is absent from scan | Check extension/category support, binary detection, configured excludes, size limits, and the diagnostics reason. Generated and vendored directories are not all excluded by name automatically. |
| Skeleton is unavailable | Confirm the language has an extractor. Python uses AST; JavaScript, TypeScript, and Go use deterministic fallback extraction. Unsupported formats and parser errors are reported. |
| Context pack reports missing metadata | Create valid `ai/MAP.md` and `ai/manifest.yml`; these are required only for subsystem context-pack generation. |
| Imported workspace path is rejected | Confirm the path remains within the policy’s selected workspace or an explicitly allowed destination. The path policy is designed to reject unsafe escapes. |
| Local UI/API is unreachable | Confirm the desktop app is running and using the configured loopback port. The local API is not intended for public network access. |

When reporting a problem, include the MergeMonster version, operating system,
Python version, exact command, and a redacted error message. Review terminal
output and exported files for local paths before sharing them.
