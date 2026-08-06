from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool

from vermay.errors import ModelProviderError
from vermay.execution_context import current_execution_context
from vermay.model_clients import OllamaModelClient, OpenAICompatibleModelClient
from vermay.tool_schema import tool_schemas_from_tools
from vermay.types import Message


@dataclass(frozen=True)
class ModelInvocation:
    """Thin project wrapper around the model's standard LangChain message."""

    message: AIMessage


class OllamaModelAdapter:
    """Adapter from the project Ollama client to LangChain standard messages."""

    def __init__(self, client: OllamaModelClient) -> None:
        self.client = client

    def invoke(self, messages: list[BaseMessage], tools: list[BaseTool]) -> ModelInvocation:
        response = self.client.invoke(
            messages=[_to_project_message(message) for message in messages],
            tools=tool_schemas_from_tools(tools),
            timeout_seconds=_execution_timeout_seconds(provider="ollama"),
        )
        if not response.tool_calls:
            return ModelInvocation(message=AIMessage(content=response.content))

        return ModelInvocation(
            message=AIMessage(
                content=response.content,
                tool_calls=[
                    {
                        "name": tool_call.name,
                        "args": tool_call.arguments,
                        "id": tool_call.id or f"call-{uuid4().hex}",
                        "type": "tool_call",
                    }
                    for tool_call in response.tool_calls
                ],
            )
        )

    def stream_text(self, messages: list[BaseMessage], tools: list[BaseTool]) -> Iterator[str]:
        yield from self.client.stream_text(
            messages=[_to_project_message(message) for message in messages],
            tools=tool_schemas_from_tools(tools),
        )


class OpenAICompatibleModelAdapter:
    """Adapter from an OpenAI-compatible client to LangChain standard messages."""

    def __init__(self, client: OpenAICompatibleModelClient) -> None:
        self.client = client

    def invoke(self, messages: list[BaseMessage], tools: list[BaseTool]) -> ModelInvocation:
        response = self.client.invoke(
            messages=[_to_project_message(message) for message in messages],
            tools=tool_schemas_from_tools(tools),
            timeout_seconds=_execution_timeout_seconds(provider="openai_compatible"),
        )
        if not response.tool_calls:
            return ModelInvocation(message=AIMessage(content=response.content))

        return ModelInvocation(
            message=AIMessage(
                content=response.content,
                tool_calls=[
                    {
                        "name": tool_call.name,
                        "args": tool_call.arguments,
                        "id": tool_call.id or f"call-{uuid4().hex}",
                        "type": "tool_call",
                    }
                    for tool_call in response.tool_calls
                ],
            )
        )


def _to_project_message(message: BaseMessage) -> Message:
    message_type = getattr(message, "type", "")
    content = str(message.content)
    if message_type == "human":
        return Message(role="user", content=content)
    if message_type == "ai":
        return Message(role="assistant", content=content, tool_calls=list(getattr(message, "tool_calls", []) or []))
    if message_type == "tool":
        return Message(
            role="tool",
            content=content,
            name=getattr(message, "name", None),
            tool_call_id=getattr(message, "tool_call_id", None),
        )
    if message_type == "system":
        return Message(role="system", content=content)
    return Message(role="user", content=content)


def _execution_timeout_seconds(*, provider: str) -> float | None:
    """Return the active Task's remaining budget for one HTTP model call.

    Direct messages run without an execution context and keep the provider's
    configured timeout. A Task can only shorten that timeout; it cannot extend
    the provider-level safety bound.
    """

    context = current_execution_context()
    if context is None:
        return None
    if context.cancellation is not None and context.cancellation.requested:
        raise ModelProviderError(
            "Model invocation was canceled before it started.",
            provider=provider,
            retryable=False,
        )
    return context.remaining_seconds()
