from __future__ import annotations

"""Legacy message context builder retained as the shared system-prompt source.

The active runtime builds LangChain `BaseMessage` context in `runtime_context`.
This builder still provides the baseline system prompt through `app_factory` and
keeps the earlier project `Message` format available for compatibility tests.
"""

from .types import Message, Observation


DEFAULT_SYSTEM_PROMPT = (
    "You are an operations assistant. Use tools when fresh runtime "
    "state is needed. Do not claim that a tool action completed "
    "unless a tool observation confirms it. For current or real "
    "Kubernetes cluster state, use SSH-backed read-only tools. "
    "For weather or forecast questions, use weather_forecast. "
    "Use request_user_input only when information required to "
    "form a tool call is missing. Do not use it to ask permission "
    "for dangerous tools; call the tool and let the runtime's "
    "permission gate request approval."
)


def default_system_prompt() -> str:
    return DEFAULT_SYSTEM_PROMPT


class ContextBuilder:
    def build(
        self,
        user_input: str,
        memory: list[str],
        skills: list[str],
        observations: list[Observation],
    ) -> list[Message]:
        messages = [
            Message(
                role="system",
                content=default_system_prompt(),
            )
        ]

        if skills:
            messages.append(Message(role="system", content="Relevant skills:\n" + "\n".join(skills)))

        if memory:
            messages.append(Message(role="system", content="Memory:\n" + "\n".join(memory)))

        messages.append(Message(role="user", content=user_input))

        for observation in observations:
            messages.append(
                Message(
                    role="tool",
                    name=observation.tool_name,
                    content=observation.content,
                )
            )

        return messages
