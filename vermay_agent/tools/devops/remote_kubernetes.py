from __future__ import annotations

import shlex

from vermay_agent.infra.ssh import SshClient

from .constants import KubectlDeleteResource, KubectlDescribeResource, KubectlGetResource


def ssh_kubectl_get(resource: str | KubectlGetResource, namespace: str = "all") -> dict:
    try:
        resource_value = KubectlGetResource(resource).value
    except ValueError as exc:
        raise ValueError(f"unsupported resource: {resource}") from exc
    args = ["get", resource_value]
    if namespace == "all":
        args.append("-A")
    else:
        args.extend(["-n", namespace])
    args.extend(["-o", "wide"])
    command = remote_kubectl_command(args)
    return SshClient().run(command)


def ssh_kubectl_describe(resource: str | KubectlDescribeResource, name: str, namespace: str = "default") -> dict:
    try:
        resource_value = KubectlDescribeResource(resource).value
    except ValueError as exc:
        raise ValueError(f"unsupported resource: {resource}") from exc
    if resource_value == "node":
        args = ["describe", "node", name]
    else:
        args = ["describe", resource_value, name, "-n", namespace]
    command = remote_kubectl_command(args)
    return SshClient(timeout_seconds=30).run(command)


def delete_resource(
    resource: str | KubectlDeleteResource,
    name: str,
    namespace: str = "default",
) -> dict:
    try:
        resource_value = KubectlDeleteResource(resource).value
    except ValueError as exc:
        raise ValueError(f"unsupported resource: {resource}") from exc
    command = remote_kubectl_command(
        ["delete", resource_value, name, "-n", namespace]
    )
    return SshClient(timeout_seconds=30).run(command)


def remote_kubectl_command(args: list[str]) -> str:
    quoted_args = " ".join(shlex.quote(arg) for arg in args)
    return (
        "PATH=/snap/bin:/usr/local/bin:/usr/bin:/bin:$PATH; "
        "if command -v kubectl >/dev/null 2>&1; then "
        f"kubectl {quoted_args}; "
        "elif command -v microk8s >/dev/null 2>&1; then "
        f"microk8s kubectl {quoted_args}; "
        "elif [ -x /snap/bin/microk8s ]; then "
        f"/snap/bin/microk8s kubectl {quoted_args}; "
        "else "
        "echo 'kubectl not found: tried kubectl, microk8s kubectl, /snap/bin/microk8s kubectl' >&2; "
        "exit 127; "
        "fi"
    )
