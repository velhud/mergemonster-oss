# Synthetic MergeMonster example

This tree is deliberately small and contains no user or customer data. It
demonstrates mixed source languages, local configuration, documentation,
generated/vendor folders, parser failure, and repository metadata for the
optional context-pack builder.

## Run the demo

From the MergeMonster repository root:

```bash
python examples/synthetic-repository/make_binary_fixture.py
mergemonster
```

Open this directory in the desktop window, scan it, choose mixed modes, inspect
the structural preview and token count, and write a merge output. The scan path
and view depend on your current UI settings; `generated/` and `vendor/` are
included to make exclusion decisions visible. The binary fixture is generated
by the script and is only 16 bytes. It is not checked into the repository.

To explore the subsystem context-pack feature, this directory includes
`ai/MAP.md` and `ai/manifest.yml`. This feature is separate from the normal
merge flow. No provider account is needed for the code/skeleton walkthrough;
new summaries are a provider-backed action.
