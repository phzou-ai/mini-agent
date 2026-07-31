from __future__ import annotations

from .tool_registry import ToolRegistry
from .tool_metadata import ApprovalPolicy, SideEffectLevel, ToolCategory, ToolMetadata
from .types import PermissionDecision, ToolCall


class PermissionPolicy:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def check(self, tool_call: ToolCall) -> PermissionDecision:
        try:
            metadata = self.registry.tool_metadata(tool_call.name)
        except KeyError:
            return PermissionDecision(
                allowed=False,
                requires_approval=False,
                reason=f"unknown tool: {tool_call.name}",
                decision="deny",
                risk_level="high",
                policy_tags=["unknown_tool"],
            )

        if metadata.approval_policy == ApprovalPolicy.DENY:
            return PermissionDecision(
                allowed=False,
                requires_approval=False,
                reason=f"tool '{tool_call.name}' is denied by policy",
                decision="deny",
                risk_level="high",
                policy_tags=["policy_deny"],
            )

        if metadata.approval_policy == ApprovalPolicy.ARGUMENT_SENSITIVE:
            argument_decision = _argument_sensitive_decision(tool_call, metadata.category)
            if argument_decision is not None:
                return argument_decision

        if metadata.approval_policy == ApprovalPolicy.APPROVAL_REQUIRED:
            return PermissionDecision(
                allowed=False,
                requires_approval=True,
                reason=_approval_reason(tool_call.name, metadata.approval_policy),
                decision="interrupt_for_approval",
                risk_level="high" if metadata.dangerous or metadata.destructive else "medium",
                approval_summary=_approval_summary(tool_call, metadata),
                safe_argument_preview=_safe_argument_preview(tool_call, metadata),
                policy_tags=_policy_tags(metadata),
            )

        return PermissionDecision(
            allowed=True,
            requires_approval=False,
            reason="safe tool",
            decision="allow",
            risk_level="low",
            policy_tags=[metadata.category.value, metadata.approval_policy.value],
        )


class PermissionGate:
    def __init__(self, registry: ToolRegistry, policy: PermissionPolicy | None = None) -> None:
        self.registry = registry
        self.policy = policy or PermissionPolicy(registry)

    def check(self, tool_call: ToolCall) -> PermissionDecision:
        return self.policy.check(tool_call)


def _argument_sensitive_decision(tool_call: ToolCall, category: ToolCategory) -> PermissionDecision | None:
    return PermissionDecision(
        allowed=False,
        requires_approval=True,
        reason=f"tool '{tool_call.name}' requires argument-sensitive approval",
        decision="interrupt_for_approval",
        risk_level="medium",
        approval_summary=f"Approve argument-sensitive tool call: {tool_call.name}",
        safe_argument_preview=dict(tool_call.arguments),
        policy_tags=[category.value, ApprovalPolicy.ARGUMENT_SENSITIVE.value],
    )


def _approval_summary(tool_call: ToolCall, metadata: ToolMetadata) -> str:
    if tool_call.name == "delete_resource" and metadata.category == ToolCategory.KUBERNETES:
        resource = _argument_text(tool_call, "resource", fallback="resource")
        name = _argument_text(tool_call, "name", fallback="unknown")
        namespace = _argument_text(tool_call, "namespace", fallback="default")
        return f"Delete Kubernetes {resource}: {namespace}/{name}"

    if tool_call.name == "kubectl_apply" and metadata.category == ToolCategory.KUBERNETES:
        manifest = _argument_text(tool_call, "manifest")
        line_count = len([line for line in manifest.splitlines() if line.strip()]) if manifest else 0
        suffix = f" ({line_count} non-empty manifest lines)" if line_count else ""
        return f"Apply Kubernetes manifest{suffix}"

    if metadata.category == ToolCategory.SHELL:
        command = _argument_text(tool_call, "command", fallback="<empty command>")
        return f"Run local shell command: {_truncate(command, 120)}"

    if metadata.destructive:
        return f"Approve destructive tool call: {tool_call.name}"

    if metadata.side_effect_level == SideEffectLevel.REMOTE:
        return f"Approve remote side-effect tool call: {tool_call.name}"

    return f"Approve tool call: {tool_call.name}"


def _safe_argument_preview(tool_call: ToolCall, metadata: ToolMetadata) -> dict[str, object]:
    if tool_call.name == "kubectl_apply" and metadata.category == ToolCategory.KUBERNETES:
        manifest = _argument_text(tool_call, "manifest")
        return {
            "manifest_preview": _truncate(manifest, 240),
            "manifest_chars": len(manifest),
            "manifest_lines": len(manifest.splitlines()) if manifest else 0,
        }

    if tool_call.name == "delete_resource" and metadata.category == ToolCategory.KUBERNETES:
        return {
            "resource": _argument_text(tool_call, "resource"),
            "name": _argument_text(tool_call, "name"),
            "namespace": _argument_text(tool_call, "namespace", fallback="default"),
        }

    if metadata.category == ToolCategory.SHELL:
        command = _argument_text(tool_call, "command")
        return {
            "command_preview": _truncate(command, 240),
            "command_chars": len(command),
        }

    return dict(tool_call.arguments)


def _policy_tags(metadata: ToolMetadata) -> list[str]:
    tags = [metadata.category.value, metadata.approval_policy.value]
    if metadata.execution_scope.value not in tags:
        tags.append(metadata.execution_scope.value)
    if metadata.side_effect_level.value not in tags:
        tags.append(metadata.side_effect_level.value)
    if metadata.destructive:
        tags.append("destructive")
    if metadata.credential_sensitive:
        tags.append("credential_sensitive")
    return tags


def _argument_text(tool_call: ToolCall, key: str, *, fallback: str = "") -> str:
    value = tool_call.arguments.get(key)
    if value is None:
        return fallback
    return str(value)


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _approval_reason(tool_name: str, approval_policy: ApprovalPolicy) -> str:
    if approval_policy == ApprovalPolicy.ARGUMENT_SENSITIVE:
        return f"tool '{tool_name}' requires argument-sensitive approval"
    return f"tool '{tool_name}' is marked dangerous"
