from __future__ import annotations

DELETE_DEFS_TOOL = {
    "type": "function",
    "name": "delete_defs",
    "description": (
        "Delete or comment out Python definitions in a file. "
        "defs should be fully-qualified names present in the file; if an item "
        "is unqualified, the tool will attempt best-effort resolution to a unique "
        "qualified name in that file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the Python file to modify.",
            },
            "defs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Qualified or unqualified names of definitions to remove from the file.",
            },
        },
        "required": ["file_path", "defs"],
        "additionalProperties": False,
    },
    "strict": True,
}

SUGGEST_STUBS_TOOL = {
    "type": "function",
    "name": "suggest_stubs",
    "description": "Return stub definitions and unit test scaffolds for unresolved symbols.",
    "parameters": {
        "type": "object",
        "properties": {
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of unresolved symbols or modules requiring stubs.",
            },
        },
        "required": ["symbols"],
        "additionalProperties": False,
    },
    "strict": True,
}
