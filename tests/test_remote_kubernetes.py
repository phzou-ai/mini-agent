import pytest

from vermay_agent.tools.devops import remote_kubernetes


def test_remote_kubectl_command_tries_kubectl_and_microk8s():
    command = remote_kubernetes.remote_kubectl_command(["get", "pods", "-A", "-o", "wide"])

    assert "PATH=/snap/bin:/usr/local/bin:/usr/bin:/bin:$PATH" in command
    assert "kubectl get pods -A -o wide" in command
    assert "microk8s kubectl get pods -A -o wide" in command
    assert "/snap/bin/microk8s kubectl get pods -A -o wide" in command


def test_ssh_kubectl_get_rejects_unsupported_resource():
    with pytest.raises(ValueError, match="unsupported resource: secrets"):
        remote_kubernetes.ssh_kubectl_get("secrets")


def test_ssh_kubectl_describe_rejects_unsupported_resource():
    with pytest.raises(ValueError, match="unsupported resource: secret"):
        remote_kubernetes.ssh_kubectl_describe("secret", "api-key")


def test_delete_resource_rejects_unsupported_resource():
    with pytest.raises(ValueError, match="unsupported resource: node"):
        remote_kubernetes.delete_resource("node", "worker-1")


def test_ssh_kubectl_get_builds_read_only_command_without_live_ssh(monkeypatch):
    calls = []

    class FakeSshClient:
        def run(self, command: str) -> dict:
            calls.append(command)
            return {"ok": True, "command": command, "stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(remote_kubernetes, "SshClient", FakeSshClient)

    result = remote_kubernetes.ssh_kubectl_get("pods", namespace="default")

    assert result["ok"] is True
    assert len(calls) == 1
    assert "kubectl get pods -n default -o wide" in calls[0]
    assert "apply" not in calls[0]
    assert "delete" not in calls[0]


def test_ssh_kubectl_describe_node_omits_namespace_without_live_ssh(monkeypatch):
    calls = []

    class FakeSshClient:
        def __init__(self, timeout_seconds: int = 20) -> None:
            self.timeout_seconds = timeout_seconds

        def run(self, command: str) -> dict:
            calls.append(command)
            return {"ok": True, "command": command, "stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(remote_kubernetes, "SshClient", FakeSshClient)

    result = remote_kubernetes.ssh_kubectl_describe("node", "phzou-nuc")

    assert result["ok"] is True
    assert len(calls) == 1
    assert "kubectl describe node phzou-nuc" in calls[0]
    assert "kubectl describe node phzou-nuc -n" not in calls[0]
    assert "microk8s kubectl describe node phzou-nuc -n" not in calls[0]


def test_delete_resource_builds_namespace_scoped_command_without_live_ssh(monkeypatch):
    calls = []

    class FakeSshClient:
        def __init__(self, timeout_seconds: int = 20) -> None:
            self.timeout_seconds = timeout_seconds

        def run(self, command: str) -> dict:
            calls.append(command)
            return {"ok": True, "command": command, "stdout": "pod deleted", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(remote_kubernetes, "SshClient", FakeSshClient)

    result = remote_kubernetes.delete_resource(
        "pod",
        "nginx-5869d7778c-687rb",
        namespace="default",
    )

    assert result["ok"] is True
    assert len(calls) == 1
    assert "kubectl delete pod nginx-5869d7778c-687rb -n default" in calls[0]
