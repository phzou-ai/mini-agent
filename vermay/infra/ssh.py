from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from vermay.env_config import load_prefixed_env
from vermay.execution_context import ExecutionContext, current_execution_context

ROOT = Path(__file__).resolve().parents[2]


class SshClient:
    def __init__(self, config_path: Path | None = None, timeout_seconds: int = 20) -> None:
        self.config_path = config_path
        self.timeout_seconds = timeout_seconds
        self.config = self._load_config()

    def run(
        self,
        remote_command: str,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> dict:
        """Run one fixed SSH capability with bounded local process control.

        This adapter deliberately controls only the local ``ssh`` child
        process. If that process is terminated after a remote write has been
        sent, the remote effect may already have happened; callers must treat
        that result as uncertain rather than automatically retrying it.
        """

        command = self._base_command() + [remote_command]
        context = execution_context or current_execution_context()
        timeout_seconds = self._effective_timeout_seconds(context)
        if context is not None and context.cancellation is not None and context.cancellation.requested:
            return self._failed_result(
                command=command,
                error_code="execution_canceled",
                error_category="execution_canceled",
                stderr="SSH command was canceled before it started.",
                retryable=False,
                outcome="canceled",
                context=context,
                timeout_seconds=timeout_seconds,
            )
        if timeout_seconds <= 0:
            return self._failed_result(
                command=command,
                error_code="execution_timeout",
                error_category="execution_timeout",
                stderr="SSH command did not start because its execution deadline had elapsed.",
                retryable=True,
                outcome="timed_out",
                context=context,
                timeout_seconds=timeout_seconds,
            )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return self._failed_result(
                command=command,
                error_code="ssh_spawn_failed",
                error_category="execution_environment",
                stderr=f"Unable to start SSH client: {exc}",
                retryable=True,
                outcome="failed",
                context=context,
            )

        started_at = time.monotonic()
        while True:
            if context is not None and context.cancellation is not None and context.cancellation.requested:
                stdout, stderr = self._stop_process(process)
                return self._failed_result(
                    command=command,
                    error_code="execution_canceled",
                    error_category="execution_canceled",
                    stdout=stdout,
                    stderr=_append_stderr(stderr, "SSH command was canceled while it was running."),
                    retryable=False,
                    outcome="canceled",
                    context=context,
                )

            remaining = timeout_seconds - (time.monotonic() - started_at)
            if remaining <= 0:
                stdout, stderr = self._stop_process(process)
                return self._failed_result(
                    command=command,
                    error_code="execution_timeout",
                    error_category="execution_timeout",
                    stdout=stdout,
                    stderr=_append_stderr(
                        stderr,
                        f"SSH command timed out after {timeout_seconds:g}s.",
                    ),
                    retryable=True,
                    outcome="timed_out",
                    context=context,
                )
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        exit_code = process.returncode
        if exit_code == 0:
            return {
                "ok": True,
                "command": self._redact_command(command),
                "stdout": stdout or "",
                "stderr": stderr or "",
                "exit_code": exit_code,
                "execution": self._execution_metadata(
                    context=context,
                    timeout_seconds=timeout_seconds,
                    outcome="completed",
                ),
            }
        return self._failed_result(
            command=command,
            error_code="ssh_command_failed",
            error_category="remote_command_failed",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            retryable=exit_code == 255,
            outcome="failed",
            context=context,
            timeout_seconds=timeout_seconds,
        )

    def _effective_timeout_seconds(self, context: ExecutionContext | None) -> float:
        timeout_seconds = float(self.timeout_seconds)
        if context is None:
            return timeout_seconds
        remaining = context.remaining_seconds()
        if remaining is None:
            return timeout_seconds
        return min(timeout_seconds, remaining)

    def _stop_process(self, process: subprocess.Popen[str]) -> tuple[str, str]:
        if process.poll() is None:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=1)
                return stdout or "", stderr or ""
            except subprocess.TimeoutExpired:
                process.kill()
        stdout, stderr = process.communicate()
        return stdout or "", stderr or ""

    def _failed_result(
        self,
        *,
        command: list[str],
        error_code: str,
        error_category: str,
        stderr: str,
        retryable: bool,
        outcome: str,
        context: ExecutionContext | None,
        stdout: str = "",
        exit_code: int | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        return {
            "ok": False,
            "command": self._redact_command(command),
            "stdout": stdout or "",
            "stderr": stderr or "",
            "exit_code": exit_code,
            "error_code": error_code,
            "error_category": error_category,
            "retryable": retryable,
            "execution": self._execution_metadata(
                context=context,
                timeout_seconds=(
                    float(self.timeout_seconds) if timeout_seconds is None else timeout_seconds
                ),
                outcome=outcome,
            ),
        }

    @staticmethod
    def _execution_metadata(
        *,
        context: ExecutionContext | None,
        timeout_seconds: float,
        outcome: str,
    ) -> dict:
        return {
            "backend": "ssh",
            "runtime_thread_id": context.runtime_thread_id if context is not None else None,
            "invocation_id": context.invocation_id if context is not None else None,
            "workspace_id": context.workspace_id if context is not None else None,
            "timeout_seconds": timeout_seconds,
            "outcome": outcome,
        }

    def _load_config(self) -> dict:
        if self.config_path is not None:
            return json.loads(self.config_path.read_text(encoding="utf-8"))

        values = load_prefixed_env("VERMAY_SSH_", root=ROOT)
        required = {
            "target": values.get("VERMAY_SSH_TARGET"),
            "port": values.get("VERMAY_SSH_PORT"),
            "identityFile": values.get("VERMAY_SSH_IDENTITY_FILE"),
            "knownHostsFile": values.get("VERMAY_SSH_KNOWN_HOSTS_FILE"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                "Missing SSH environment config: "
                + ", ".join(missing)
                + ". Define VERMAY_SSH_* in .env.local using .env as the template."
            )

        return {
            "target": required["target"],
            "port": int(str(required["port"])),
            "identityFile": required["identityFile"],
            "knownHostsFile": required["knownHostsFile"],
        }

    def _base_command(self) -> list[str]:
        config = self.config
        command = [
            "ssh",
            "-p",
            str(config["port"]),
            "-i",
            str(Path(config["identityFile"]).expanduser()),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=yes",
            "-o",
            f"UserKnownHostsFile={Path(config['knownHostsFile']).expanduser()}",
            config["target"],
        ]
        return command

    def _redact_command(self, command: list[str]) -> str:
        redacted = []
        skip_next = False
        for part in command:
            if skip_next:
                redacted.append("<identity-file>")
                skip_next = False
                continue
            redacted.append(part)
            if part == "-i":
                skip_next = True
        return " ".join(redacted)


def _append_stderr(current: str | None, message: str) -> str:
    value = current or ""
    return f"{value}\n{message}".strip()
