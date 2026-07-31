from __future__ import annotations

import json
from typing import Any


def parse_json_decision(content: str) -> dict[str, Any] | None:
    normalized = strip_markdown_json_fence(content.strip())
    try:
        decision = json.loads(normalized)
    except json.JSONDecodeError:
        decision = extract_embedded_action(normalized)
    if not isinstance(decision, dict):
        return None
    return normalize_known_tool_action(decision)


def normalize_known_tool_action(decision: dict[str, Any]) -> dict[str, Any]:
    """Normalize model shorthand for project-owned tools."""
    if decision.get("action") != "request_user_input":
        return decision

    raw_arguments = decision.get("arguments")
    arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
    for key in ("prompt", "choices", "inputSchema"):
        if key in decision and key not in arguments:
            arguments[key] = decision[key]

    return {
        "action": "tool_call",
        "name": "request_user_input",
        "arguments": arguments,
    }


def strip_markdown_json_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content


def extract_embedded_action(content: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "action" in candidate:
            return candidate
    return None
