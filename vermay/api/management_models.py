from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RegisteredAgentUpsertRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    card_url: str = Field(min_length=1)
    card_json: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisteredAgentResponse(BaseModel):
    agent_id: str
    name: str
    card_url: str
    card_json: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ModelSelectionResponse(BaseModel):
    name: str
    provider: str
    model: str | None = None
    base_url: str | None = None
    timeout_seconds: int | float | str | None = None


class ModelConfigResponse(BaseModel):
    primary_model: ModelSelectionResponse
    router_model: ModelSelectionResponse
    router_model_overridden: bool = False
    config_path: str


class ContextUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
