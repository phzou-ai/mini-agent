from vermay.permission import PermissionGate, PermissionPolicy
from vermay.tool_registry import ToolRegistry
from vermay.tooling import ToolArgs, structured_tool
from vermay.tool_metadata import ApprovalPolicy
from vermay.tools.devops import register_devops_tools
from vermay.types import ToolCall


class EmptyArgs(ToolArgs):
    pass


class PathArgs(ToolArgs):
    path: str


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        structured_tool(
            func=lambda: "ok",
            name="safe_tool",
            description="Safe test tool.",
            args_schema=EmptyArgs,
            dangerous=False,
        )
    )
    registry.register(
        structured_tool(
            func=lambda: "not executed",
            name="dangerous_tool",
            description="Dangerous test tool.",
            args_schema=EmptyArgs,
            dangerous=True,
        )
    )
    registry.register(
        structured_tool(
            func=lambda: "denied",
            name="denied_tool",
            description="Denied test tool.",
            args_schema=EmptyArgs,
            dangerous=False,
            approval_policy=ApprovalPolicy.DENY,
        )
    )
    return registry


def test_safe_tool_is_allowed_without_approval():
    decision = PermissionGate(make_registry()).check(ToolCall(name="safe_tool"))

    assert decision.allowed is True
    assert decision.requires_approval is False
    assert decision.reason == "safe tool"
    assert decision.decision == "allow"
    assert decision.risk_level == "low"


def test_dangerous_tool_requires_approval():
    decision = PermissionGate(make_registry()).check(ToolCall(name="dangerous_tool"))

    assert decision.allowed is False
    assert decision.requires_approval is True
    assert "dangerous_tool" in decision.reason
    assert decision.decision == "interrupt_for_approval"
    assert decision.approval_summary == "Approve tool call: dangerous_tool"
    assert "approval_required" in decision.policy_tags


def test_permission_policy_denies_policy_denied_tool():
    decision = PermissionPolicy(make_registry()).check(ToolCall(name="denied_tool"))

    assert decision.allowed is False
    assert decision.requires_approval is False
    assert decision.decision == "deny"
    assert "denied_tool" in decision.reason
    assert "policy_deny" in decision.policy_tags


def test_permission_policy_denies_unknown_tool():
    decision = PermissionPolicy(make_registry()).check(ToolCall(name="missing_tool"))

    assert decision.allowed is False
    assert decision.requires_approval is False
    assert decision.decision == "deny"
    assert "unknown tool" in decision.reason
    assert "unknown_tool" in decision.policy_tags


def test_permission_policy_requires_approval_for_argument_sensitive_tool():
    registry = ToolRegistry()
    registry.register(
        structured_tool(
            func=lambda path: path,
            name="read_project_file",
            description="Test-only argument-sensitive file read.",
            args_schema=PathArgs,
            dangerous=False,
            approval_policy=ApprovalPolicy.ARGUMENT_SENSITIVE,
        )
    )

    decision = PermissionPolicy(registry).check(
        ToolCall(name="read_project_file", arguments={"path": "README.md"})
    )

    assert decision.allowed is False
    assert decision.requires_approval is True
    assert decision.decision == "interrupt_for_approval"
    assert "argument-sensitive" in decision.reason
    assert decision.safe_argument_preview == {"path": "README.md"}
    assert "argument_sensitive" in decision.policy_tags


def test_permission_policy_enriches_delete_resource_approval_prompt():
    registry = ToolRegistry()
    register_devops_tools(registry)

    decision = PermissionPolicy(registry).check(
        ToolCall(name="delete_resource", arguments={"resource": "deployment", "name": "api"})
    )

    assert decision.requires_approval is True
    assert decision.risk_level == "high"
    assert decision.approval_summary == "Delete Kubernetes deployment: default/api"
    assert decision.safe_argument_preview == {
        "resource": "deployment",
        "name": "api",
        "namespace": "default",
    }
    assert "destructive" in decision.policy_tags
