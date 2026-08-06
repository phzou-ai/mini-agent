from __future__ import annotations

from typing import Iterator, Protocol

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from vermay.langgraph_runtime.nodes import GraphModelClient
from vermay.system_prompt import default_system_prompt

from .context import text_from_parts, to_langchain_message
from .models import MessageRecord


class LocalMessageResponder(Protocol):
    def respond(self, messages: list[MessageRecord]) -> list[dict]: ...


class DirectModelLocalMessageResponder:
    def __init__(self, model: GraphModelClient, *, system_prompt: str | None = None) -> None:
        self.model = model
        self.system_prompt = system_prompt or default_system_prompt()

    def respond(self, messages: list[MessageRecord]) -> list[dict]:
        invocation = self.model.invoke(messages=self._model_messages(messages), tools=[])
        content = _string_content(invocation.message)
        return [{"kind": "text", "text": content}]

    def stream(self, messages: list[MessageRecord]) -> Iterator[str]:
        stream_text = getattr(self.model, "stream_text", None)
        if not callable(stream_text):
            yield _text_from_parts(self.respond(messages))
            return
        yield from stream_text(self._model_messages(messages), [])

    def _model_messages(self, messages: list[MessageRecord]) -> list[BaseMessage]:
        return [SystemMessage(content=self.system_prompt), *[to_langchain_message(message) for message in messages]]


def _string_content(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return str(content)
