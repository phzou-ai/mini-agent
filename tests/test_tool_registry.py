import pytest
from pydantic import Field

from vermay_agent.tool_registry import ToolRegistry
from vermay_agent.tooling import ToolArgs, structured_tool
from vermay_agent.tool_metadata import ApprovalPolicy, ExecutionScope, SideEffectLevel, ToolCategory
from vermay_agent.tool_schema import DEFAULT_OUTPUT_MAX_CHARS
from vermay_agent.tools.devops import register_devops_tools
from vermay_agent.tools.devops.constants import (
    KUBECTL_DELETE_RESOURCES,
    KUBECTL_DESCRIBE_RESOURCES,
    KUBECTL_GET_RESOURCES,
)
from vermay_agent.tools.weather import register_weather_tools


class SampleArgs(ToolArgs):
    value: str = Field(description="Sample value.")


def make_sample_tool(
    dangerous: bool = False,
    *,
    name: str = "sample",
    approval_policy: str | ApprovalPolicy | None = None,
    category: str = "unknown",
    execution_scope: str = "unknown",
    redaction_required: bool = False,
    output_visibility: str = "internal",
    output_redaction_status: str = "unknown",
    output_max_chars: int = DEFAULT_OUTPUT_MAX_CHARS,
):
    return structured_tool(
        func=lambda value: value,
        name=name,
        description="Sample tool.",
        args_schema=SampleArgs,
        dangerous=dangerous,
        approval_policy=approval_policy,
        category=category,
        execution_scope=execution_scope,
        redaction_required=redaction_required,
        output_visibility=output_visibility,
        output_redaction_status=output_redaction_status,
        output_max_chars=output_max_chars,
    )


def test_registry_exposes_schema_from_structured_tool_args_schema():
    registry = ToolRegistry()
    registry.register(make_sample_tool())

    schema = registry.schemas()[0]

    assert registry.names() == ["sample"]
    assert schema["name"] == "sample"
    assert schema["description"] == "Sample tool."
    assert schema["dangerous"] is False
    assert schema["parameters"]["properties"]["value"]["type"] == "string"
    assert schema["parameters"]["properties"]["value"]["description"] == "Sample value."
    assert schema["parameters"]["required"] == ["value"]


def test_registry_exposes_dangerous_metadata():
    registry = ToolRegistry()
    registry.register(make_sample_tool(dangerous=True))

    assert registry.is_dangerous("sample") is True
    assert registry.schemas()[0]["dangerous"] is True


def test_registry_exposes_default_tool_output_policy():
    registry = ToolRegistry()
    registry.register(make_sample_tool())

    assert registry.output_policy("sample") == {
        "visibility": "internal",
        "redaction_status": "unknown",
        "max_chars": DEFAULT_OUTPUT_MAX_CHARS,
    }


def test_registry_exposes_overridden_tool_output_policy():
    registry = ToolRegistry()
    registry.register(
        make_sample_tool(
            output_visibility="public",
            output_redaction_status="clean",
            output_max_chars=200,
        )
    )

    assert registry.output_policy("sample") == {
        "visibility": "public",
        "redaction_status": "clean",
        "max_chars": 200,
    }


def test_registry_exposes_validated_tool_metadata():
    registry = ToolRegistry()
    registry.register(
        make_sample_tool(
            category="filesystem",
            execution_scope="local",
            redaction_required=True,
        )
    )

    metadata = registry.tool_metadata("sample")

    assert metadata.category == ToolCategory.FILESYSTEM
    assert metadata.execution_scope == ExecutionScope.LOCAL
    assert metadata.approval_policy == ApprovalPolicy.AUTO
    assert metadata.redaction_required is True


def test_structured_tool_rejects_invalid_output_policy():
    with pytest.raises(ValueError, match="output_visibility"):
        make_sample_tool(output_visibility="external")


def test_registry_rejects_duplicate_tool_names():
    registry = ToolRegistry()
    tool = make_sample_tool()

    registry.register(tool)

    with pytest.raises(ValueError, match="tool already registered: sample"):
        registry.register(tool)


def test_registry_unknown_tool_has_clear_error():
    registry = ToolRegistry()

    with pytest.raises(KeyError, match="unknown tool: missing"):
        registry.get("missing")


def test_registry_model_facing_tools_exclude_policy_denied_tools():
    registry = ToolRegistry()
    registry.register(make_sample_tool(name="safe_tool"))
    registry.register(make_sample_tool(name="approval_tool", dangerous=True))
    registry.register(make_sample_tool(name="denied_tool", approval_policy=ApprovalPolicy.DENY))

    assert registry.names() == ["approval_tool", "denied_tool", "safe_tool"]
    assert [tool.name for tool in registry.tools_for_model()] == ["approval_tool", "safe_tool"]


def test_devops_tool_schemas_use_single_source_resource_enums():
    registry = ToolRegistry()
    register_devops_tools(registry)
    schemas = {schema["name"]: schema for schema in registry.schemas()}

    get_resource_schema = schemas["ssh_kubectl_get"]["parameters"]["$defs"]["KubectlGetResource"]
    describe_resource_schema = schemas["ssh_kubectl_describe"]["parameters"]["$defs"]["KubectlDescribeResource"]
    delete_resource_schema = schemas["delete_resource"]["parameters"]["$defs"]["KubectlDeleteResource"]

    assert get_resource_schema["enum"] == KUBECTL_GET_RESOURCES
    assert describe_resource_schema["enum"] == KUBECTL_DESCRIBE_RESOURCES
    assert delete_resource_schema["enum"] == KUBECTL_DELETE_RESOURCES


def test_devops_tools_have_explicit_metadata_classification():
    registry = ToolRegistry()
    register_devops_tools(registry)

    ssh_get = registry.tool_metadata("ssh_kubectl_get")
    assert ssh_get.category == ToolCategory.KUBERNETES
    assert ssh_get.execution_scope == ExecutionScope.REMOTE
    assert ssh_get.read_only is True
    assert ssh_get.side_effect_level == SideEffectLevel.NONE
    assert ssh_get.approval_policy == ApprovalPolicy.AUTO
    assert ssh_get.credential_sensitive is True
    assert ssh_get.redaction_required is True

    delete_resource = registry.tool_metadata("delete_resource")
    assert delete_resource.category == ToolCategory.KUBERNETES
    assert delete_resource.execution_scope == ExecutionScope.REMOTE
    assert delete_resource.read_only is False
    assert delete_resource.side_effect_level == SideEffectLevel.DESTRUCTIVE
    assert delete_resource.approval_policy == ApprovalPolicy.APPROVAL_REQUIRED
    assert delete_resource.destructive is True
    assert delete_resource.dangerous is True


def test_weather_tool_has_external_network_read_only_metadata():
    registry = ToolRegistry()
    register_weather_tools(registry)

    metadata = registry.tool_metadata("weather_forecast")

    assert metadata.category == ToolCategory.WEATHER
    assert metadata.execution_scope == ExecutionScope.EXTERNAL_NETWORK
    assert metadata.read_only is True
    assert metadata.side_effect_level == SideEffectLevel.NONE
    assert metadata.approval_policy == ApprovalPolicy.AUTO
    assert metadata.redaction_required is False
