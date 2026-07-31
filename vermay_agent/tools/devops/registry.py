from __future__ import annotations

from vermay_agent.tool_registry import ToolRegistry
from vermay_agent.tooling import ToolArgs, structured_tool
from vermay_agent.tool_metadata import ApprovalPolicy, ExecutionScope, SideEffectLevel, ToolCategory
from pydantic import Field

from .constants import KubectlDeleteResource, KubectlDescribeResource, KubectlGetResource
from .dangerous import exec_shell, kubectl_apply
from .remote_kubernetes import delete_resource, ssh_kubectl_describe, ssh_kubectl_get


class SshKubectlGetArgs(ToolArgs):
    resource: KubectlGetResource = Field(description="Kubernetes resource type to read.")
    namespace: str = Field(default="all", description="Kubernetes namespace or 'all'.")


class SshKubectlDescribeArgs(ToolArgs):
    resource: KubectlDescribeResource = Field(description="Kubernetes resource type to describe.")
    name: str = Field(description="Kubernetes resource name.")
    namespace: str = Field(default="default", description="Kubernetes namespace. Ignored for node.")


class ExecShellArgs(ToolArgs):
    command: str = Field(description="Shell command to execute.")


class KubectlApplyArgs(ToolArgs):
    manifest: str = Field(description="Kubernetes manifest YAML or JSON.")


class DeleteResourceArgs(ToolArgs):
    resource: KubectlDeleteResource = Field(description="Kubernetes resource type.")
    name: str = Field(description="Kubernetes resource name.")
    namespace: str = Field(default="default", description="Kubernetes namespace.")


def register_devops_tools(registry: ToolRegistry) -> None:
    registry.register(
        structured_tool(
            func=ssh_kubectl_get,
            name="ssh_kubectl_get",
            description=(
                "Read current real Kubernetes cluster state over SSH. Prefer this for current, real, "
                "remote, or live cluster questions. This is read-only."
            ),
            args_schema=SshKubectlGetArgs,
            dangerous=False,
            category=ToolCategory.KUBERNETES,
            execution_scope=ExecutionScope.REMOTE,
            read_only=True,
            side_effect_level=SideEffectLevel.NONE,
            approval_policy=ApprovalPolicy.AUTO,
            credential_sensitive=True,
            redaction_required=True,
        )
    )
    registry.register(
        structured_tool(
            func=ssh_kubectl_describe,
            name="ssh_kubectl_describe",
            description=(
                "Describe a Kubernetes resource over SSH. Read-only. Use after ssh_kubectl_get "
                "when detailed status/events are needed."
            ),
            args_schema=SshKubectlDescribeArgs,
            dangerous=False,
            category=ToolCategory.KUBERNETES,
            execution_scope=ExecutionScope.REMOTE,
            read_only=True,
            side_effect_level=SideEffectLevel.NONE,
            approval_policy=ApprovalPolicy.AUTO,
            credential_sensitive=True,
            redaction_required=True,
        )
    )
    registry.register(
        structured_tool(
            func=exec_shell,
            name="exec_shell",
            description="Execute a shell command. Dangerous and requires approval.",
            args_schema=ExecShellArgs,
            dangerous=True,
            category=ToolCategory.SHELL,
            execution_scope=ExecutionScope.LOCAL,
            read_only=False,
            side_effect_level=SideEffectLevel.UNKNOWN,
            approval_policy=ApprovalPolicy.APPROVAL_REQUIRED,
            redaction_required=True,
        )
    )
    registry.register(
        structured_tool(
            func=kubectl_apply,
            name="kubectl_apply",
            description="Apply a Kubernetes manifest. Dangerous and requires approval.",
            args_schema=KubectlApplyArgs,
            dangerous=True,
            category=ToolCategory.KUBERNETES,
            execution_scope=ExecutionScope.REMOTE,
            read_only=False,
            side_effect_level=SideEffectLevel.REMOTE,
            approval_policy=ApprovalPolicy.APPROVAL_REQUIRED,
            credential_sensitive=True,
            redaction_required=True,
        )
    )
    registry.register(
        structured_tool(
            func=delete_resource,
            name="delete_resource",
            description=(
                "Delete a real Kubernetes resource over SSH. Dangerous and requires "
                "explicit operator approval."
            ),
            args_schema=DeleteResourceArgs,
            dangerous=True,
            category=ToolCategory.KUBERNETES,
            execution_scope=ExecutionScope.REMOTE,
            read_only=False,
            side_effect_level=SideEffectLevel.DESTRUCTIVE,
            approval_policy=ApprovalPolicy.APPROVAL_REQUIRED,
            destructive=True,
            credential_sensitive=True,
            redaction_required=True,
        )
    )
