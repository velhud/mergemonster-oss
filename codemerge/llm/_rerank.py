from __future__ import annotations

import json
from typing import List, Tuple

from ..logger import get_logger
from ..prune.assets import load_json_asset
from .clients import openai_available, get_openai_client
from ._responses import extract_text
from .accounting import record_provider_call

logger = get_logger(__name__)


def rerank_with_llm(
    goal: str,
    file_items: List[Tuple[str, float]],
    *,
    client: object | None,
    model_name: str = "gpt-5-mini",
) -> List[str]:
    if not openai_available():
        raise RuntimeError("OpenAI library not installed (required for LLM rerank).")
    if client is None:
        client = get_openai_client()
    schema = load_json_asset("schema_rerank.json")
    payload = [{"path": p, "score": s} for p, s in file_items]
    prompt = {
        "role": "user",
        "content": json.dumps({
            "goal": goal,
            "files": payload,
            "instruction": "Return ordered_paths as the best order of files matching the goal. Only include listed paths.",
        }),
    }
    format_dict = {
        "type": "json_schema",
        "name": schema["name"],
        "schema": schema["schema"],
    }
    if schema.get("strict"):
        format_dict["strict"] = True
    text_config = {"format": format_dict}
    record_provider_call(provider="openai", operation="responses.create", model=model_name)
    res = client.responses.create(
        model=model_name,
        input=[prompt],
        text=text_config,
        parallel_tool_calls=False,
    )
    data = json.loads(extract_text(res) or "{}")
    return data.get("ordered_paths", [])
