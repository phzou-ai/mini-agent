from __future__ import annotations

from typing import Protocol

from vermay.types import Message, ModelResponse


class ModelClient(Protocol):
    def invoke(self, messages: list[Message], tools: list[dict]) -> ModelResponse: ...

