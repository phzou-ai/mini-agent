from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class A2AMessage(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    role: str | None = None
    parts: list[dict[str, Any]] = Field(default_factory=list)
    message_id: str | None = Field(default=None, alias="messageId")
    task_id: str | None = Field(default=None, alias="taskId")
    context_id: str | None = Field(default=None, alias="contextId")
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2AJsonRpcMessageSendRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    jsonrpc: Literal["2.0"]
    method: Literal["message/send"]
    params: dict[str, Any]
    id: Any = None
