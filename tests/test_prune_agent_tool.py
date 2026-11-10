import os
import sys
import json
import types
import logging
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from codemerge.agent import run_pruning_agent


class DummyClient:
    def __init__(self, call_args, plan=None):
        self._call_args = call_args
        # If plan is provided, we will return it. Otherwise, we will synthesize
        # a plan from the tool call arguments to emulate model behavior.
        self._plan = plan

        def create(**kwargs):
            call = {
                "id": "call-1",
                "type": "tool_call",
                "function": {
                    "name": "delete_defs",
                    "arguments": json.dumps(self._call_args),
                },
            }
            return types.SimpleNamespace(id="resp-1", output=[call], output_text="")

        def submit_tool_outputs(response_id, tool_outputs):
            if self._plan is None:
                synthesized = {
                    "deletions": [
                        {
                            "file_path": self._call_args.get("file_path", ""),
                            "defs": self._call_args.get("defs", []),
                        }
                    ]
                }
                plan_out = synthesized
            else:
                plan_out = self._plan
            return types.SimpleNamespace(
                id=response_id, output=[], output_text=json.dumps(plan_out)
            )

        self.responses = types.SimpleNamespace(
            create=create, submit_tool_outputs=submit_tool_outputs
        )


def test_agent_handles_tool_call(tmp_path, caplog):
    file_path = Path(tmp_path) / "mod.py"
    file_path.write_text(
        "def keep():\n    pass\n\n\n"
        "def remove():\n    pass\n"
    )

    expected_plan = {
        "deletions": [{"file_path": str(file_path), "defs": ["remove"]}]
    }
    client = DummyClient({"file_path": str(file_path), "defs": ["remove"]}, expected_plan)

    with caplog.at_level(logging.INFO):
        result = run_pruning_agent("clean", {"symbols": []}, client=client)

    content = file_path.read_text()
    assert "def remove" not in content
    assert "def keep" in content
    assert result == expected_plan
    assert any('"event": "delete_defs"' in r.message for r in caplog.records)
    assert any('"event": "final_pruning_plan"' in r.message for r in caplog.records)
