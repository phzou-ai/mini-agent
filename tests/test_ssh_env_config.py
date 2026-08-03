import json
from pathlib import Path

import pytest

from vermay_agent.execution_context import ExecutionCancellation, ExecutionContext
from vermay_agent.infra import ssh as ssh_module
from vermay_agent.infra.ssh import SshClient


SSH_ENV_KEYS = (
    "TARGET",
    "PORT",
    "IDENTITY_FILE",
    "KNOWN_HOSTS_FILE",
)


def clear_ssh_env(monkeypatch):
    for prefix in ("VERMAY_AGENT_SSH_", "MINI_AGENT_SSH_"):
        for key in SSH_ENV_KEYS:
            monkeypatch.delenv(prefix + key, raising=False)


def test_ssh_client_loads_env_local_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ssh_module, "ROOT", tmp_path)
    clear_ssh_env(monkeypatch)
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "VERMAY_AGENT_SSH_TARGET=user@example-host",
                "VERMAY_AGENT_SSH_PORT=2222",
                "VERMAY_AGENT_SSH_IDENTITY_FILE=~/.ssh/example",
                "VERMAY_AGENT_SSH_KNOWN_HOSTS_FILE=~/.ssh/known_hosts",
            ]
        ),
        encoding="utf-8",
    )

    client = SshClient()

    assert client.config == {
        "target": "user@example-host",
        "port": 2222,
        "identityFile": "~/.ssh/example",
        "knownHostsFile": "~/.ssh/known_hosts",
    }


def test_ssh_client_loads_deprecated_mini_agent_env_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ssh_module, "ROOT", tmp_path)
    clear_ssh_env(monkeypatch)
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "MINI_AGENT_SSH_TARGET=user@example-host",
                "MINI_AGENT_SSH_PORT=2222",
                "MINI_AGENT_SSH_IDENTITY_FILE=~/.ssh/example",
                "MINI_AGENT_SSH_KNOWN_HOSTS_FILE=~/.ssh/known_hosts",
            ]
        ),
        encoding="utf-8",
    )

    client = SshClient()

    assert client.config == {
        "target": "user@example-host",
        "port": 2222,
        "identityFile": "~/.ssh/example",
        "knownHostsFile": "~/.ssh/known_hosts",
    }


def test_ssh_client_uses_fixed_safe_host_key_options(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ssh_module, "ROOT", tmp_path)
    clear_ssh_env(monkeypatch)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "VERMAY_AGENT_SSH_TARGET=user@example-host",
                "VERMAY_AGENT_SSH_PORT=2222",
                "VERMAY_AGENT_SSH_IDENTITY_FILE=~/.ssh/example",
                "VERMAY_AGENT_SSH_KNOWN_HOSTS_FILE=~/.ssh/known_hosts",
            ]
        ),
        encoding="utf-8",
    )

    command = SshClient()._base_command()

    assert "StrictHostKeyChecking=yes" in command
    assert "UpdateHostKeys=yes" in command


def test_ssh_client_env_vars_override_env_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ssh_module, "ROOT", tmp_path)
    clear_ssh_env(monkeypatch)
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "VERMAY_AGENT_SSH_TARGET=file-host",
                "VERMAY_AGENT_SSH_PORT=22",
                "VERMAY_AGENT_SSH_IDENTITY_FILE=~/.ssh/file",
                "VERMAY_AGENT_SSH_KNOWN_HOSTS_FILE=~/.ssh/known_hosts",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VERMAY_AGENT_SSH_TARGET", "env-host")
    monkeypatch.setenv("VERMAY_AGENT_SSH_PORT", "2200")
    monkeypatch.setenv("VERMAY_AGENT_SSH_IDENTITY_FILE", "~/.ssh/env")
    monkeypatch.setenv("VERMAY_AGENT_SSH_KNOWN_HOSTS_FILE", "~/.ssh/env_known_hosts")

    client = SshClient()

    assert client.config["target"] == "env-host"
    assert client.config["port"] == 2200
    assert client.config["identityFile"] == "~/.ssh/env"
    assert client.config["knownHostsFile"] == "~/.ssh/env_known_hosts"


def test_ssh_client_prefers_vermay_env_over_deprecated_mini_agent_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ssh_module, "ROOT", tmp_path)
    clear_ssh_env(monkeypatch)
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "VERMAY_AGENT_SSH_TARGET=new-file-host",
                "VERMAY_AGENT_SSH_PORT=2222",
                "VERMAY_AGENT_SSH_IDENTITY_FILE=~/.ssh/new",
                "VERMAY_AGENT_SSH_KNOWN_HOSTS_FILE=~/.ssh/new_known_hosts",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINI_AGENT_SSH_TARGET", "legacy-env-host")
    monkeypatch.setenv("MINI_AGENT_SSH_PORT", "2200")
    monkeypatch.setenv("MINI_AGENT_SSH_IDENTITY_FILE", "~/.ssh/legacy")
    monkeypatch.setenv("MINI_AGENT_SSH_KNOWN_HOSTS_FILE", "~/.ssh/legacy_known_hosts")

    client = SshClient()

    assert client.config == {
        "target": "new-file-host",
        "port": 2222,
        "identityFile": "~/.ssh/new",
        "knownHostsFile": "~/.ssh/new_known_hosts",
    }


def test_ssh_client_reports_missing_env_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ssh_module, "ROOT", tmp_path)
    clear_ssh_env(monkeypatch)

    with pytest.raises(ValueError, match="Missing SSH environment config"):
        SshClient()


def test_ssh_client_attaches_execution_context_to_a_completed_command(tmp_path: Path, monkeypatch):
    config_path = _write_ssh_config(tmp_path)
    process = _FakeCompletedProcess(stdout="pods", stderr="", returncode=0)
    monkeypatch.setattr(ssh_module.subprocess, "Popen", lambda *args, **kwargs: process)
    client = SshClient(config_path=config_path, timeout_seconds=30)

    result = client.run(
        "kubectl get pods",
        execution_context=ExecutionContext(
            runtime_thread_id="thread-r3",
            invocation_id="inv-r3",
        ),
    )

    assert result["ok"] is True
    assert result["stdout"] == "pods"
    assert result["execution"] == {
        "backend": "ssh",
        "runtime_thread_id": "thread-r3",
        "invocation_id": "inv-r3",
        "workspace_id": None,
        "timeout_seconds": 30.0,
        "outcome": "completed",
    }
    assert "<identity-file>" in result["command"]


def test_ssh_client_does_not_start_a_canceled_execution(tmp_path: Path, monkeypatch):
    config_path = _write_ssh_config(tmp_path)
    called = False

    def unexpected_popen(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Popen must not run for a canceled execution")

    monkeypatch.setattr(ssh_module.subprocess, "Popen", unexpected_popen)
    cancellation = ExecutionCancellation.create()
    cancellation.request("operator canceled")
    client = SshClient(config_path=config_path)

    result = client.run(
        "kubectl delete pod api-1",
        execution_context=ExecutionContext(
            runtime_thread_id="thread-r3",
            cancellation=cancellation,
        ),
    )

    assert called is False
    assert result["ok"] is False
    assert result["error_code"] == "execution_canceled"
    assert result["execution"]["outcome"] == "canceled"


def test_ssh_client_terminates_an_active_process_after_cancellation(tmp_path: Path, monkeypatch):
    config_path = _write_ssh_config(tmp_path)
    cancellation = ExecutionCancellation.create()
    process = _CancelOnFirstPollProcess(cancellation)
    monkeypatch.setattr(ssh_module.subprocess, "Popen", lambda *args, **kwargs: process)
    client = SshClient(config_path=config_path)

    result = client.run(
        "kubectl delete pod api-1",
        execution_context=ExecutionContext(
            runtime_thread_id="thread-r3",
            cancellation=cancellation,
        ),
    )

    assert process.terminated is True
    assert result["ok"] is False
    assert result["error_code"] == "execution_canceled"
    assert result["execution"]["outcome"] == "canceled"


def test_ssh_client_terminates_an_active_process_after_timeout(tmp_path: Path, monkeypatch):
    config_path = _write_ssh_config(tmp_path)
    process = _NeverCompletesProcess()
    monotonic_values = iter((0.0, 0.0, 1.1))
    monkeypatch.setattr(ssh_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(ssh_module.subprocess, "Popen", lambda *args, **kwargs: process)
    client = SshClient(config_path=config_path, timeout_seconds=1)

    result = client.run("kubectl get pods")

    assert process.terminated is True
    assert result["ok"] is False
    assert result["error_code"] == "execution_timeout"
    assert result["retryable"] is True
    assert result["execution"]["outcome"] == "timed_out"


def _write_ssh_config(tmp_path: Path) -> Path:
    path = tmp_path / "ssh.json"
    path.write_text(
        json.dumps(
            {
                "target": "user@example-host",
                "port": 2222,
                "identityFile": "~/.ssh/example",
                "knownHostsFile": "~/.ssh/known_hosts",
            }
        ),
        encoding="utf-8",
    )
    return path


class _FakeCompletedProcess:
    def __init__(self, *, stdout: str, stderr: str, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        del timeout
        return self.stdout, self.stderr

    def poll(self) -> int:
        return self.returncode


class _CancelOnFirstPollProcess:
    def __init__(self, cancellation: ExecutionCancellation) -> None:
        self.cancellation = cancellation
        self.returncode: int | None = None
        self.terminated = False
        self._communicate_calls = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self._communicate_calls += 1
        if self._communicate_calls == 1:
            self.cancellation.request("operator canceled")
            raise ssh_module.subprocess.TimeoutExpired("ssh", timeout)
        return "", ""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class _NeverCompletesProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self._communicate_calls = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self._communicate_calls += 1
        if self._communicate_calls == 1:
            raise ssh_module.subprocess.TimeoutExpired("ssh", timeout)
        return "", ""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9
