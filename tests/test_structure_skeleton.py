from __future__ import annotations

from codemerge.structure import extract_file_structure, render_skeleton_block
from codemerge.structure.cache import StructureCache


def _tokens(text: str) -> int:
    return len(text.split())


def test_python_ast_skeleton_extracts_signatures_decorators_docstrings_and_constants(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "sample.py"
    path.write_text(
        "\n".join(
            [
                "import os",
                "from typing import Any",
                "",
                "PUBLIC_CONST = 42",
                "private_value = 3",
                "",
                "@decorator",
                "class Service:",
                "    \"\"\"Service docs.",
                "    Hidden details.",
                "    \"\"\"",
                "",
                "    @classmethod",
                "    async def build(cls, value: Any) -> 'Service':",
                "        return cls()",
                "",
                "    def run(self, item):",
                "        print(item)",
                "",
                "async def top_level(name: str) -> None:",
                "    \"\"\"Top docs.\"\"\"",
                "    print(name)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    view = extract_file_structure(
        path,
        root=str(root),
        token_counter=_tokens,
        cache=StructureCache(tmp_path / "structure.db"),
    )

    assert view.parser_status == "ok"
    assert view.language == "python"
    assert "import os" in view.skeleton_text
    assert "from typing import Any" in view.skeleton_text
    assert "PUBLIC_CONST = 42" in view.skeleton_text
    assert "private_value" not in view.skeleton_text
    assert "@decorator" in view.skeleton_text
    assert "class Service:" in view.skeleton_text
    assert '"""Service docs."""' in view.skeleton_text
    assert "@classmethod" in view.skeleton_text
    assert "async def build(cls, value: Any) -> 'Service':" in view.skeleton_text
    assert "def run(self, item):" in view.skeleton_text
    assert "async def top_level(name: str) -> None:" in view.skeleton_text
    assert "return cls()" not in view.skeleton_text
    assert "print(name)" not in view.skeleton_text
    assert {sym.name for sym in view.symbols} == {"Service", "build", "run", "top_level"}


def test_python_ast_invalid_syntax_renders_explicit_error(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "broken.py"
    path.write_text("def nope(:\n    pass\n", encoding="utf-8")

    view = extract_file_structure(
        path,
        root=str(root),
        token_counter=_tokens,
        cache=StructureCache(tmp_path / "structure.db"),
    )
    rendered = render_skeleton_block(view)

    assert view.parser_status == "error"
    assert "Python syntax error" in rendered
    assert "# Skeleton unavailable for this file." in rendered
    assert "pass" not in rendered


def test_typescript_fallback_skeleton_extracts_structural_surface(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "component.ts"
    path.write_text(
        "\n".join(
            [
                "import React from 'react';",
                "import type { User } from './types';",
                "",
                "export interface Props {",
                "  user: User;",
                "}",
                "",
                "export const API_URL = '/v1';",
                "",
                "export class Widget {",
                "  readonly name: string;",
                "  constructor(name: string) {",
                "    this.name = name;",
                "  }",
                "  render(user: User): string {",
                "    return user.name;",
                "  }",
                "}",
                "",
                "export const makeWidget = (name: string) => {",
                "  return new Widget(name);",
                "};",
            ]
        ),
        encoding="utf-8",
    )

    view = extract_file_structure(
        path,
        root=str(root),
        token_counter=_tokens,
        cache=StructureCache(tmp_path / "structure.db"),
    )
    rendered = render_skeleton_block(view)

    assert view.language == "typescript"
    assert view.parser_status == "fallback"
    assert "deterministic JS/TS fallback extractor" in rendered
    assert "import React from 'react';" in view.skeleton_text
    assert "import type { User } from './types';" in view.skeleton_text
    assert "export interface Props" in view.skeleton_text
    assert "export const API_URL = '/v1';" in view.skeleton_text
    assert "export class Widget {" in view.skeleton_text
    assert "constructor(name: string) { ... }" in view.skeleton_text
    assert "render(user: User): string { ... }" in view.skeleton_text
    assert "export const makeWidget" in view.skeleton_text
    assert "return user.name" not in view.skeleton_text
    assert "return new Widget" not in view.skeleton_text
    assert {"Props", "API_URL", "Widget", "constructor", "render", "makeWidget"}.issubset(
        {sym.name for sym in view.symbols}
    )


def test_javascript_fallback_skeleton_extracts_functions_and_classes(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "app.js"
    path.write_text(
        "\n".join(
            [
                "import express from 'express';",
                "export const PORT = 3000;",
                "export function createApp() {",
                "  const app = express();",
                "  return app;",
                "}",
                "class Router {",
                "  route(path) {",
                "    return path;",
                "  }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    view = extract_file_structure(
        path,
        root=str(root),
        token_counter=_tokens,
        cache=StructureCache(tmp_path / "structure.db"),
    )

    assert view.parser_status == "fallback"
    assert "import express from 'express';" in view.skeleton_text
    assert "export const PORT = 3000;" in view.skeleton_text
    assert "export function createApp() { ... }" in view.skeleton_text
    assert "class Router {" in view.skeleton_text
    assert "route(path) { ... }" in view.skeleton_text
    assert "const app = express" not in view.skeleton_text
    assert "return path" not in view.skeleton_text


def test_go_fallback_skeleton_extracts_package_imports_types_and_funcs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "service.go"
    path.write_text(
        "\n".join(
            [
                "package service",
                "",
                "import (",
                '  "context"',
                '  "fmt"',
                ")",
                "",
                "// Handler processes requests.",
                "type Handler struct {",
                "  Name string",
                "}",
                "",
                "const Version = \"v1\"",
                "",
                "func NewHandler(name string) *Handler {",
                "  return &Handler{Name: name}",
                "}",
                "",
                "func (h *Handler) Serve(ctx context.Context) error {",
                "  fmt.Println(h.Name)",
                "  return nil",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    view = extract_file_structure(
        path,
        root=str(root),
        token_counter=_tokens,
        cache=StructureCache(tmp_path / "structure.db"),
    )

    assert view.language == "go"
    assert view.parser_status == "fallback"
    assert "package service" in view.skeleton_text
    assert 'import ( "context" "fmt" )' in view.skeleton_text
    assert "type Handler struct" in view.skeleton_text
    assert 'const Version = "v1"' in view.skeleton_text
    assert "func NewHandler(name string) *Handler { ... }" in view.skeleton_text
    assert "func (h *Handler) Serve(ctx context.Context) error { ... }" in view.skeleton_text
    assert "fmt.Println" not in view.skeleton_text
    assert "return nil" not in view.skeleton_text


def test_unsupported_language_renders_unavailable_diagnostic(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "thing.rb"
    path.write_text("def run\n  puts 'body'\nend\n", encoding="utf-8")

    view = extract_file_structure(
        path,
        root=str(root),
        token_counter=_tokens,
        cache=StructureCache(tmp_path / "structure.db"),
    )
    rendered = render_skeleton_block(view)

    assert view.language == "ruby"
    assert view.parser_status == "unsupported"
    assert "No skeleton extractor is available" in rendered
    assert "# Skeleton unavailable for this file." in rendered
    assert "puts 'body'" not in rendered


def test_structure_cache_is_keyed_by_source_freshness(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "cached.py"
    cache = StructureCache(tmp_path / "structure.db")

    path.write_text("def first():\n    pass\n", encoding="utf-8")
    first = extract_file_structure(path, root=str(root), token_counter=_tokens, cache=cache)

    path.write_text("def second():\n    pass\n", encoding="utf-8")
    second = extract_file_structure(path, root=str(root), token_counter=_tokens, cache=cache)

    assert "def first():" in first.skeleton_text
    assert "def second():" in second.skeleton_text
    assert first.source_sha256 != second.source_sha256
