# Provider configuration

Repository scanning, Python and fallback structural extraction, token preview,
and source-based context exports do not require an AI provider. Creating new
summaries or invoking AI compare requires provider configuration and network
access.

## Groq summaries

1. Install the optional provider extra:

   ```bash
   python -m pip install -e '.[providers]'
   ```
2. Set `GROQ_API_KEY` in the process environment before launching the desktop
   app. The value is a placeholder below; do not commit a real credential:

   ```bash
   export GROQ_API_KEY="replace-with-your-own-key"
   mergemonster
   ```

3. In Settings, choose a supported value for `groq_summary_model`. The current
   public default is `openai/gpt-oss-20b`; use only model identifiers currently
   supported by Groq.
4. Review the provider preflight scope and confirmation before starting a
   provider-backed operation.

MergeMonster sends summary prompts and selected source content to the chosen
Groq model. It does not automatically discover `.env` files, read a private
credential store, or fall back to `OPENAI_API_KEY` when `GROQ_API_KEY` is
missing. If a separately configured OpenAI retrieval or embedding feature is
available, its credential is only for that separate path and is never used as
a summary fallback.
Missing credentials or an unsupported model should produce a clear error.

The provider selection is explicit. No alternative provider is selected
silently. Live provider operation is not represented as tested by this
documentation; provider-backed checks should be run separately with a test
account and a bounded synthetic scope.

## Configuration storage

User configuration defaults to `~/.mergemonster`; `MERGEMONSTER_HOME` selects
another home directory. `CODEMERGE_HOME` and `~/.codemerge` are compatibility
locations. Keep credentials in the process environment and keep generated
configuration local. Example configuration files in this repository contain
placeholders only.

## Optional retrieval and embeddings

The source tree also contains separate OpenAI retrieval or embedding paths.
They are distinct from Groq summaries, are not required for the offline first
run, and should be used only when explicitly configured and available in the
installed package. Do not infer their provider from the summary provider
setting.
