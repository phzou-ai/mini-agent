from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from .tool_metadata import (
    ApprovalPolicy,
    ExecutionScope,
    SideEffectLevel,
    ToolCategory,
    ToolSource,
    metadata_from_legacy,
)
from .tool_schema import (
    DEFAULT_OUTPUT_MAX_CHARS,
    DEFAULT_OUTPUT_REDACTION_STATUS,
    DEFAULT_OUTPUT_VISIBILITY,
)


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def structured_tool(
    *,
    func: Callable[..., Any],
    name: str,
    description: str,
    args_schema: type[BaseModel],
    dangerous: bool = False,
    source: ToolSource | str = ToolSource.BUILTIN,
    category: ToolCategory | str = ToolCategory.UNKNOWN,
    execution_scope: ExecutionScope | str = ExecutionScope.UNKNOWN,
    read_only: bool | None = None,
    side_effect_level: SideEffectLevel | str = SideEffectLevel.UNKNOWN,
    approval_policy: ApprovalPolicy | str | None = None,
    destructive: bool = False,
    credential_sensitive: bool = False,
    argument_policy: str | dict[str, Any] | None = None,
    output_visibility: str = DEFAULT_OUTPUT_VISIBILITY,
    output_redaction_status: str = DEFAULT_OUTPUT_REDACTION_STATUS,
    redaction_required: bool = False,
    output_max_chars: int = DEFAULT_OUTPUT_MAX_CHARS,
    produces_artifacts: bool = False,
    artifact_kinds: list[str] | tuple[str, ...] = (),
) -> StructuredTool:
    metadata = metadata_from_legacy(
        dangerous=dangerous,
        source=source,
        category=category,
        execution_scope=execution_scope,
        read_only=read_only,
        side_effect_level=side_effect_level,
        approval_policy=approval_policy,
        destructive=destructive,
        credential_sensitive=credential_sensitive,
        argument_policy=argument_policy,
        output_visibility=output_visibility,
        output_redaction_status=output_redaction_status,
        redaction_required=redaction_required,
        output_max_chars=output_max_chars,
        produces_artifacts=produces_artifacts,
        artifact_kinds=artifact_kinds,
    ).to_metadata()
    return StructuredTool.from_function(
        func=func,
        name=name,
        description=description,
        args_schema=args_schema,
        metadata=metadata,
    )
