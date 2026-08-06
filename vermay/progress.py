from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console


class ProgressReporter:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._last_loop: int | None = None
        self._console = Console(file=sys.stderr, highlight=False)

    def event(self, step: int | None, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        if event.endswith("_start") and event != "model_call_start" and event != "tool_execute_start":
            return

        self._format_event(step, event, fields)

    def _format_event(self, step: int | None, event: str, fields: dict[str, Any]) -> None:
        if event == "run_started":
            self._last_loop = None
            self._console.print(f"> {self._input_summary(str(fields['input']))}", style="bold")
            self._print_field("max_steps", fields["max_steps"], indent=0)
            return
        if event == "context_built":
            self._step_header(step)
            roles = []
            for message in fields["message_preview"]:
                role = message["role"]
                if message.get("name"):
                    role += f":{message['name']}"
                roles.append(role)
            self._print_event_line(
                "context",
                {
                    "messages": fields["messages"],
                    "observations": fields["observations"],
                    "roles": " -> ".join(roles),
                },
            )
            return
        if event == "model_call_start":
            self._step_header(step)
            self._print_event_line("model_call", {"status": "calling"})
            return
        if event == "model_response":
            self._step_header(step)
            if fields.get("tool"):
                self._print_event_line("model_decision", {"action": "tool_call", "tool": fields["tool"]})
                return
            self._print_event_line(
                "model_decision",
                {"action": "final", "summary": self._content_summary(fields["content"])},
            )
            return
        if event == "tool_call":
            self._step_header(step)
            payload = fields["payload"]
            name = payload.get("name") if isinstance(payload, dict) else None
            args = payload.get("arguments") if isinstance(payload, dict) else payload
            self._print_event_line("tool_call", {"name": name, "args": self._args_summary(args)})
            return
        if event == "permission":
            self._step_header(step)
            status = "allowed" if fields["allowed"] else "blocked"
            self._print_event_line(
                "permission",
                {"status": status, "approval": fields["approval"], "reason": fields["reason"]},
            )
            return
        if event == "tool_execute_start":
            self._step_header(step)
            self._print_event_line("execute", {"tool": fields["tool"]})
            return
        if event == "tool_result":
            self._step_header(step)
            payload = {"tool": fields["tool"], "ok": fields["ok"]}
            if fields.get("exit_code") is not None:
                payload["exit_code"] = fields["exit_code"]
            if fields.get("command_summary"):
                payload["command"] = self._preview(str(fields["command_summary"]), 220, True)
            self._print_event_line("result", payload)
            return
        if event == "observation":
            self._step_header(step)
            self._print_event_line(
                "observation",
                {
                    "tool": fields["tool"],
                    "ok": fields["ok"],
                    "summary": self._summary_preview(fields["summary"]),
                },
            )
            return
        if event == "final_answer":
            self._step_header(step)
            self._print_event_line("done", {"status": "final_answer"})
            print("", file=sys.stderr, flush=True)
            return
        if event == "approval_required":
            self._step_header(step)
            self._print_event_line("approval", {"status": "required", "tool": fields["tool"]})
            return
        if event == "approval_resumed":
            self._step_header(step)
            self._print_event_line("approval", {"status": "resumed", "tool": fields["tool"]})
            return
        if event == "max_steps_reached":
            self._print_event_line("stopped", {"max_steps": fields["max_steps"]})
            return
        if not event.endswith("_start"):
            self._step_header(step)
            self._print_event_line(event, {"data": self._preview(str(fields), 220, True)})

    def _step_header(self, step: int | None) -> None:
        if step is None or self._last_loop == step:
            return
        if self._last_loop is not None:
            print("", file=sys.stderr, flush=True)
        self._console.print(f"loop {step}", style="bold cyan")
        self._last_loop = step

    def _print_event_line(self, name: str, fields: dict[str, Any]) -> None:
        self._console.print(f"  {name}", end="", style="bold green")
        for key, value in fields.items():
            self._console.print("  ", end="")
            self._console.print(f"{key}=", end="", style="dim")
            self._console.print(self._render_inline_value(value), end="")
        self._console.print()

    def _print_field(self, key: str, value: Any, indent: int = 4, style: str | None = None) -> None:
        rendered = self._render_scalar(value)
        prefix = " " * indent
        if "\n" not in rendered:
            self._console.print(f"{prefix}{key}: ", end="", style="dim")
            self._console.print(rendered, style=style)
            return

        self._console.print(f"{prefix}{key}:", style="dim")
        for line in rendered.splitlines():
            self._console.print(f"{prefix}  {line}", style=style)

    def _print_mapping(self, key: str, value: Any) -> None:
        if not isinstance(value, dict):
            self._print_field(key, self._value_summary(value))
            return

        self._console.print(f"    {key}:", style="dim")
        if not value:
            self._console.print("      {}", style="dim")
            return
        for item_key, item_value in value.items():
            self._print_field(str(item_key), self._value_summary(item_value), indent=6)

    def _render_scalar(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)) or value is None:
            return json.dumps(value, ensure_ascii=False)
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(value)

    def _render_inline_value(self, value: Any) -> str:
        if isinstance(value, str):
            return self._preview(value, 220, single_line=True)
        if isinstance(value, (int, float, bool)) or value is None:
            return json.dumps(value, ensure_ascii=False)
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            rendered = str(value)
        return self._preview(rendered, 220, single_line=True)

    def _preview(self, value: str, limit: int = 500, single_line: bool = False) -> str:
        text = value.replace("\n", "\\n") if single_line else value
        if len(text) > limit:
            return text[:limit] + "...<truncated>"
        return text

    def _input_summary(self, value: str) -> str:
        lines = value.splitlines()
        first_line = lines[0] if lines else value
        if "\n" in value:
            return f"{self._preview(first_line, 90, single_line=True)} <{len(value)} chars, {len(lines)} lines>"
        if len(value) > 120:
            return f"{self._preview(value, 90, single_line=True)} <{len(value)} chars>"
        return value

    def _args_summary(self, value: Any) -> str:
        if isinstance(value, dict):
            parts = []
            for key, item in value.items():
                parts.append(f"{key}={self._value_summary(item)}")
            return "{" + ", ".join(parts) + "}"
        return self._value_summary(value)

    def _value_summary(self, value: Any) -> str:
        if isinstance(value, str):
            line_count = value.count("\n") + 1
            if "\n" in value:
                return f"<{len(value)} chars, {line_count} lines>"
            if len(value) > 80:
                return f"<{len(value)} chars>"
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, (int, float, bool)) or value is None:
            return json.dumps(value, ensure_ascii=False)
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            rendered = str(value)
        if len(rendered) > 120:
            return f"<{type(value).__name__}, {len(rendered)} chars>"
        return rendered

    def _content_summary(self, value: Any) -> str:
        text = str(value)
        if "\n" in text:
            lines = text.splitlines()
            first_line = lines[0] if lines else ""
            return f"{self._preview(first_line, 120, single_line=True)} <{len(text)} chars, {len(lines)} lines>"
        return self._preview(text, 180, single_line=True)

    def _summary_preview(self, value: Any) -> str:
        text = str(value)
        if "\n" in text:
            lines = text.splitlines()
            first_line = lines[0] if lines else ""
            return f"{self._preview(first_line, 120, single_line=True)} <{len(text)} chars, {len(lines)} lines>"
        return self._preview(text, 180, single_line=True)
