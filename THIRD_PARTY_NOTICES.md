# Third-party notices

MergeMonster's first-party code is MIT-licensed. The bundled tokenizer data retains its Apache-2.0 license. The wheel uses the license expression `MIT AND Apache-2.0` for the combined distribution.

## Bundled material

- **Tokenizer ranks.** `codemerge/core/data/o200k_base.tiktoken.gz` contains the mergeable ranks extracted from OpenAI's [gpt-oss-20b tokenizer](https://huggingface.co/openai/gpt-oss-20b/blob/b82898b76ce197ee12a6fa2c5e98f262329865c0/tokenizer.json), licensed under [Apache-2.0](LICENSES/gpt-oss-Apache-2.0.txt). The conversion reverses the byte-level character mapping, serializes base64 token and integer rank pairs in rank order, and gzip-compresses the result. The uncompressed SHA-256 is `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`, equal to the upstream tiktoken reference. The tokenizer source JSON SHA-256 is `0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3`.
- **Tokenizer pattern and special-token definitions.** `codemerge/core/tokens.py` adapts the `o200k_base` definitions from [OpenAI tiktoken](https://github.com/openai/tiktoken/blob/main/tiktoken_ext/openai_public.py). Copyright 2022 OpenAI and Shantanu Jain. See the [MIT license text](LICENSES/tiktoken-MIT.txt).

The UI uses system fonts. Structural extraction uses Python's standard library AST and built-in declaration extractors for JavaScript, TypeScript and Go.

## Runtime dependencies

The package manager installs these libraries separately. Each distribution supplies its own license and attribution.

| Dependency | License | Project |
| --- | --- | --- |
| FastAPI | MIT | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| Uvicorn | BSD-3-Clause | [encode/uvicorn](https://github.com/encode/uvicorn) |
| Pydantic | MIT | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| PyYAML | MIT | [yaml/pyyaml](https://github.com/yaml/pyyaml) |
| tiktoken | MIT | [openai/tiktoken](https://github.com/openai/tiktoken) |
| pywebview (desktop extra) | BSD-3-Clause | [r0x0r/pywebview](https://github.com/r0x0r/pywebview) |
| OpenAI Python SDK (providers extra) | Apache-2.0 | [openai/openai-python](https://github.com/openai/openai-python) |
