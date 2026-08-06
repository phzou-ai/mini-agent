from __future__ import annotations

from copy import deepcopy
from typing import Any

from langchain_core.tools import BaseTool


DANGEROUS_METADATA_KEY = "dangerous"
OUTPUT_VISIBILITY_METADATA_KEY = "output_visibility"
OUTPUT_REDACTION_STATUS_METADATA_KEY = "output_redaction_status"
OUTPUT_MAX_CHARS_METADATA_KEY = "output_max_chars"
DEFAULT_OUTPUT_VISIBILITY = "internal"
DEFAULT_OUTPUT_REDACTION_STATUS = "unknown"
DEFAULT_OUTPUT_MAX_CHARS = 1000


def tool_schema_from_tool(tool: BaseTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool_parameters_schema(tool),
        "dangerous": bool((tool.metadata or {}).get(DANGEROUS_METADATA_KEY, False)),
    }


def tool_schemas_from_tools(tools: list[BaseTool]) -> list[dict[str, Any]]:
    return [tool_schema_from_tool(tool) for tool in tools]


def tool_parameters_schema(tool: BaseTool) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        if isinstance(args_schema, dict):
            return deepcopy(args_schema)
        if hasattr(args_schema, "model_json_schema"):
            return args_schema.model_json_schema()

    return {
        "type": "object",
        "properties": deepcopy(getattr(tool, "args", {}) or {}),
    }
