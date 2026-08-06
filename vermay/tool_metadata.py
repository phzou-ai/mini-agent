from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .tool_schema import (
    DANGEROUS_METADATA_KEY,
    DEFAULT_OUTPUT_MAX_CHARS,
    DEFAULT_OUTPUT_REDACTION_STATUS,
    DEFAULT_OUTPUT_VISIBILITY,
    OUTPUT_MAX_CHARS_METADATA_KEY,
    OUTPUT_REDACTION_STATUS_METADATA_KEY,
    OUTPUT_VISIBILITY_METADATA_KEY,
)


SOURCE_METADATA_KEY = "source"
CATEGORY_METADATA_KEY = "category"
EXECUTION_SCOPE_METADATA_KEY = "execution_scope"
READ_ONLY_METADATA_KEY = "read_only"
SIDE_EFFECT_LEVEL_METADATA_KEY = "side_effect_level"
APPROVAL_POLICY_METADATA_KEY = "approval_policy"
DESTRUCTIVE_METADATA_KEY = "destructive"
CREDENTIAL_SENSITIVE_METADATA_KEY = "credential_sensitive"
ARGUMENT_POLICY_METADATA_KEY = "argument_policy"
REDACTION_REQUIRED_METADATA_KEY = "redaction_required"
PRODUCES_ARTIFACTS_METADATA_KEY = "produces_artifacts"
ARTIFACT_KINDS_METADATA_KEY = "artifact_kinds"


class ToolSource(str, Enum):
    BUILTIN = "builtin"
    MCP = "mcp"
    UNKNOWN = "unknown"


class ToolCategory(str, Enum):
    FILESYSTEM = "filesystem"
    LOGS = "logs"
    KUBERNETES = "kubernetes"
    SHELL = "shell"
    WEATHER = "weather"
    MCP = "mcp"
    UNKNOWN = "unknown"


class ExecutionScope(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    EXTERNAL_NETWORK = "external_network"
    MCP = "mcp"
    UNKNOWN = "unknown"


class SideEffectLevel(str, Enum):
    NONE = "none"
    LOCAL = "local"
    REMOTE = "remote"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


class ApprovalPolicy(str, Enum):
    AUTO = "auto"
    APPROVAL_REQUIRED = "approval_required"
    ARGUMENT_SENSITIVE = "argument_sensitive"
    DENY = "deny"


@dataclass(frozen=True)
class ToolMetadata:
    source: ToolSource = ToolSource.BUILTIN
    category: ToolCategory = ToolCategory.UNKNOWN
    execution_scope: ExecutionScope = ExecutionScope.UNKNOWN
    read_only: bool = True
    side_effect_level: SideEffectLevel = SideEffectLevel.UNKNOWN
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    destructive: bool = False
    credential_sensitive: bool = False
    argument_policy: str | dict[str, Any] | None = None
    output_visibility: str = DEFAULT_OUTPUT_VISIBILITY
    output_redaction_status: str = DEFAULT_OUTPUT_REDACTION_STATUS
    redaction_required: bool = False
    output_max_chars: int = DEFAULT_OUTPUT_MAX_CHARS
    produces_artifacts: bool = False
    artifact_kinds: tuple[str, ...] = field(default_factory=tuple)
    dangerous: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            SOURCE_METADATA_KEY: self.source.value,
            CATEGORY_METADATA_KEY: self.category.value,
            EXECUTION_SCOPE_METADATA_KEY: self.execution_scope.value,
            READ_ONLY_METADATA_KEY: self.read_only,
            SIDE_EFFECT_LEVEL_METADATA_KEY: self.side_effect_level.value,
            APPROVAL_POLICY_METADATA_KEY: self.approval_policy.value,
            DESTRUCTIVE_METADATA_KEY: self.destructive,
            CREDENTIAL_SENSITIVE_METADATA_KEY: self.credential_sensitive,
            ARGUMENT_POLICY_METADATA_KEY: self.argument_policy,
            OUTPUT_VISIBILITY_METADATA_KEY: self.output_visibility,
            OUTPUT_REDACTION_STATUS_METADATA_KEY: self.output_redaction_status,
            REDACTION_REQUIRED_METADATA_KEY: self.redaction_required,
            OUTPUT_MAX_CHARS_METADATA_KEY: self.output_max_chars,
            PRODUCES_ARTIFACTS_METADATA_KEY: self.produces_artifacts,
            ARTIFACT_KINDS_METADATA_KEY: list(self.artifact_kinds),
            DANGEROUS_METADATA_KEY: self.dangerous,
        }


def metadata_from_legacy(metadata: dict[str, Any] | None = None, **overrides: Any) -> ToolMetadata:
    values: dict[str, Any] = dict(metadata or {})
    values.update({key: value for key, value in overrides.items() if value is not None})

    dangerous = _bool_value(values.get(DANGEROUS_METADATA_KEY, False), DANGEROUS_METADATA_KEY)
    read_only = _bool_value(values.get(READ_ONLY_METADATA_KEY, not dangerous), READ_ONLY_METADATA_KEY)
    approval_policy = _enum_value(
        values.get(
            APPROVAL_POLICY_METADATA_KEY,
            ApprovalPolicy.APPROVAL_REQUIRED.value if dangerous else ApprovalPolicy.AUTO.value,
        ),
        ApprovalPolicy,
        APPROVAL_POLICY_METADATA_KEY,
    )

    if approval_policy == ApprovalPolicy.APPROVAL_REQUIRED:
        dangerous = True
    elif approval_policy == ApprovalPolicy.AUTO and DANGEROUS_METADATA_KEY not in values:
        dangerous = False

    tool_metadata = ToolMetadata(
        source=_enum_value(values.get(SOURCE_METADATA_KEY, ToolSource.BUILTIN.value), ToolSource, SOURCE_METADATA_KEY),
        category=_enum_value(
            values.get(CATEGORY_METADATA_KEY, ToolCategory.UNKNOWN.value),
            ToolCategory,
            CATEGORY_METADATA_KEY,
        ),
        execution_scope=_enum_value(
            values.get(EXECUTION_SCOPE_METADATA_KEY, ExecutionScope.UNKNOWN.value),
            ExecutionScope,
            EXECUTION_SCOPE_METADATA_KEY,
        ),
        read_only=read_only,
        side_effect_level=_enum_value(
            values.get(SIDE_EFFECT_LEVEL_METADATA_KEY, SideEffectLevel.UNKNOWN.value),
            SideEffectLevel,
            SIDE_EFFECT_LEVEL_METADATA_KEY,
        ),
        approval_policy=approval_policy,
        destructive=_bool_value(values.get(DESTRUCTIVE_METADATA_KEY, False), DESTRUCTIVE_METADATA_KEY),
        credential_sensitive=_bool_value(
            values.get(CREDENTIAL_SENSITIVE_METADATA_KEY, False),
            CREDENTIAL_SENSITIVE_METADATA_KEY,
        ),
        argument_policy=values.get(ARGUMENT_POLICY_METADATA_KEY),
        output_visibility=str(values.get(OUTPUT_VISIBILITY_METADATA_KEY, DEFAULT_OUTPUT_VISIBILITY)),
        output_redaction_status=str(
            values.get(OUTPUT_REDACTION_STATUS_METADATA_KEY, DEFAULT_OUTPUT_REDACTION_STATUS)
        ),
        redaction_required=_bool_value(
            values.get(REDACTION_REQUIRED_METADATA_KEY, False),
            REDACTION_REQUIRED_METADATA_KEY,
        ),
        output_max_chars=_positive_int(
            values.get(OUTPUT_MAX_CHARS_METADATA_KEY, DEFAULT_OUTPUT_MAX_CHARS),
            OUTPUT_MAX_CHARS_METADATA_KEY,
        ),
        produces_artifacts=_bool_value(
            values.get(PRODUCES_ARTIFACTS_METADATA_KEY, False),
            PRODUCES_ARTIFACTS_METADATA_KEY,
        ),
        artifact_kinds=_string_tuple(values.get(ARTIFACT_KINDS_METADATA_KEY, ()), ARTIFACT_KINDS_METADATA_KEY),
        dangerous=dangerous,
    )
    return validate_tool_metadata(tool_metadata)


def validate_tool_metadata(metadata: ToolMetadata) -> ToolMetadata:
    _validate_output_visibility(metadata.output_visibility)
    _validate_redaction_status(metadata.output_redaction_status)
    if metadata.output_max_chars < 1:
        raise ValueError("output_max_chars must be positive")
    if metadata.artifact_kinds and not metadata.produces_artifacts:
        raise ValueError("artifact_kinds requires produces_artifacts=true")
    if metadata.destructive and metadata.side_effect_level != SideEffectLevel.DESTRUCTIVE:
        raise ValueError("destructive=true requires side_effect_level=destructive")
    if metadata.destructive and metadata.read_only:
        raise ValueError("destructive=true requires read_only=false")
    if metadata.read_only and metadata.side_effect_level in {
        SideEffectLevel.LOCAL,
        SideEffectLevel.REMOTE,
        SideEffectLevel.DESTRUCTIVE,
    }:
        raise ValueError("read_only=true requires side_effect_level=none or unknown")
    if metadata.output_visibility in {"public", "a2a"} and metadata.output_redaction_status not in {
        "clean",
        "redacted",
    }:
        raise ValueError("public/a2a output visibility requires clean or redacted output")
    return metadata


def _enum_value(value: Any, enum_type: type[Enum], key: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{key} must be one of: {allowed}") from exc


def _bool_value(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{key} must be a boolean")


def _positive_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if result < 1:
        raise ValueError(f"{key} must be a positive integer")
    return result


def _string_tuple(value: Any, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        items = value
    elif isinstance(value, list):
        items = tuple(value)
    else:
        raise ValueError(f"{key} must be a list of strings")
    result = tuple(str(item) for item in items)
    if any(not item for item in result):
        raise ValueError(f"{key} cannot contain empty values")
    return result


def _validate_output_visibility(value: str) -> None:
    allowed = {"public", "local_api", "a2a", "ui", "internal", "trace_only"}
    if value not in allowed:
        raise ValueError(f"output_visibility must be one of: {', '.join(sorted(allowed))}")


def _validate_redaction_status(value: str) -> None:
    allowed = {"clean", "redacted", "unsafe", "unknown"}
    if value not in allowed:
        raise ValueError(f"output_redaction_status must be one of: {', '.join(sorted(allowed))}")
