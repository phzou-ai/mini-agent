from __future__ import annotations

from pydantic import Field

from vermay_agent.tool_registry import ToolRegistry
from vermay_agent.tooling import ToolArgs, structured_tool


REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"


class RequestUserInputArgs(ToolArgs):
    prompt: str = Field(description="A concise question that tells the user what information is required.")
    choices: list[str] = Field(
        default_factory=list,
        description="Optional short choices when the answer should be selected from a fixed set.",
    )


def register_user_input_tool(registry: ToolRegistry) -> None:
    registry.register(
        structured_tool(
            func=_request_user_input,
            name=REQUEST_USER_INPUT_TOOL_NAME,
            description=(
                "Pause the current task and ask the user for missing information. "
                "Use this only when the task cannot continue safely or correctly without the user's answer."
            ),
            args_schema=RequestUserInputArgs,
            dangerous=False,
            read_only=True,
        )
    )


def _request_user_input(prompt: str, choices: list[str] | None = None) -> str:
    # The LangGraph permission node intercepts this tool before execution.
    return prompt if not choices else f"{prompt} Choices: {', '.join(choices)}"
