from __future__ import annotations

import json
import re
from typing import Any


def parse_json_decision(content: str) -> dict[str, Any] | None:
    normalized = strip_markdown_json_fence(content.strip())
    decision = decode_json_object(normalized)
    if decision is None:
        normalized = strip_markdown_json_fence(strip_reasoning_markup(normalized).strip())
        decision = decode_json_object(normalized)
    if decision is None:
        decision = extract_embedded_action(normalized)
    if not isinstance(decision, dict):
        return None
    return normalize_known_tool_action(decision)


def decode_json_object(content: str) -> dict[str, Any] | None:
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        return None
    return decision if isinstance(decision, dict) else None


_THINK_BLOCK = re.compile(r"<think(?:\s[^>]*)?>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_THINK_TAG = re.compile(r"</?think(?:\s[^>]*)?>", re.IGNORECASE)


def strip_reasoning_markup(content: str) -> str:
    """Remove model-private reasoning markup before extracting an embedded action."""
    return _THINK_TAG.sub("", _THINK_BLOCK.sub("", content))


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
