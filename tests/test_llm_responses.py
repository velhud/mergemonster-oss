from __future__ import annotations

from types import SimpleNamespace

from codemerge.llm._responses import extract_json, extract_text


def test_extract_text_prefers_output_text_when_present() -> None:
    response = SimpleNamespace(output_text="summary", output=[])
    assert extract_text(response) == "summary"


def test_extract_text_falls_back_to_output_tree_when_output_text_is_empty() -> None:
    response = SimpleNamespace(
        output_text="",
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(text="folder "),
                    SimpleNamespace(text="summary"),
                ]
            )
        ],
    )

    assert extract_text(response) == "folder summary"


def test_extract_json_uses_output_tree_when_output_text_is_empty() -> None:
    response = SimpleNamespace(
        output_text="",
        output=[
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"status":"ok","score":5}',
                    }
                ]
            }
        ],
    )

    assert extract_json(response) == {"status": "ok", "score": 5}
