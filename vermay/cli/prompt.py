from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from vermay.langgraph_runtime import LangGraphAgentRuntime, ModelProviderConfig
from vermay.model_selection import resolve_model_selection
from vermay.mcp.transport import MCPTransportError

from ..app_factory import DEFAULT_MODEL_CONFIG_PATH, ROOT, RuntimeFactoryConfig, build_runtime


def run_prompt(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Vermay")
    parser.add_argument("prompt", nargs="*", help="User input")
    parser.add_argument(
        "--trace",
        default="latest.jsonl",
        help="Trace JSONL filename or path under traces/. Absolute paths are allowed.",
    )
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG_PATH), help="Model selection config path")
    parser.add_argument("--model", default=None, help="Configured model name to use")
    parser.add_argument("--max-steps", type=int, default=5, help="Maximum model calls per run")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress logs on stderr")
    parser.add_argument(
        "--mcp-server",
        action="append",
        default=[],
        help="Select a configured MCP server for this run. Can be repeated.",
    )
    parser.add_argument(
        "--mcp-resource",
        action="append",
        default=[],
        help="Read and inject a selected MCP resource for this run. Can be repeated.",
    )
    parser.add_argument(
        "--mcp-prompt",
        action="append",
        default=[],
        help="Read and inject selected MCP prompt guidance for this run. Can be repeated.",
    )
    parser.add_argument("--thread-id", default=None, help="LangGraph checkpoint thread id")
    parser.add_argument(
        "--resume-approval",
        choices=["true", "false"],
        default=None,
        help="Resume a LangGraph approval interrupt with approval true or false",
    )
    parser.add_argument("--approval-reason", default=None, help="Optional reason for approval resume")
    args = parser.parse_args(argv)

    user_input = " ".join(args.prompt).strip() or "check cluster status"
    try:
        model_config = _model_provider_config_from_args(args)
        trace_path = _trace_path(args.trace)
    except (ValueError, MCPTransportError) as exc:
        parser.error(str(exc))

    try:
        runtime = build_runtime(
            RuntimeFactoryConfig(
                model=model_config,
                model_config_path=Path(args.model_config),
                trace_path=trace_path,
                max_loops=args.max_steps,
                show_progress=not args.no_progress,
                mcp_servers=tuple(args.mcp_server),
                mcp_prompts=tuple(args.mcp_prompt),
                mcp_resources=tuple(args.mcp_resource),
            )
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        if args.resume_approval is not None:
            if not args.thread_id:
                raise SystemExit("--thread-id is required with --resume-approval")
            approved = args.resume_approval == "true"
            print(runtime.resume(thread_id=args.thread_id, approved=approved, reason=args.approval_reason).to_output())
            return

        if sys.stdin.isatty():
            print(
                run_langgraph_with_interactive_approval(
                    runtime,
                    user_input,
                    _prompt_for_approval,
                    thread_id=args.thread_id,
                )
            )
            return

        print(runtime.run(user_input, thread_id=args.thread_id))
    except MCPTransportError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        runtime.close()


def run_langgraph_with_interactive_approval(
    runtime: LangGraphAgentRuntime,
    user_input: str,
    approval_provider: Callable[[str, str], tuple[bool, str | None]],
    max_approval_rounds: int = 1,
    thread_id: str | None = None,
) -> str:
    result = runtime.start(user_input, thread_id=thread_id)
    approval_rounds = 0

    while result.interrupt_message is not None:
        approval_rounds += 1
        if approval_rounds > max_approval_rounds:
            message = f"Stopped after {max_approval_rounds} approval rounds."
            if runtime.trace is not None:
                runtime.trace.log_event("langgraph_approval_round_limit_reached", {"message": message})
            return message

        approved, reason = approval_provider(result.interrupt_message, result.thread_id)
        result = runtime.resume(thread_id=result.thread_id, approved=approved, reason=reason)

    return result.to_output()


def _prompt_for_approval(message: str, thread_id: str) -> tuple[bool, str | None]:
    for line in message.splitlines():
        if not line.startswith("Resume with:"):
            print(line)
    while True:
        try:
            value = input(f"Approve tool execution for thread {thread_id}? [yes/no]: ").strip().lower()
        except EOFError:
            return False, "approval input unavailable"
        if value in {"y", "yes"}:
            return True, "approved interactively"
        if value in {"n", "no"}:
            return False, "rejected interactively"
        print("Please enter yes or no.")


def _model_provider_config_from_args(args: argparse.Namespace) -> ModelProviderConfig | None:
    model_name = getattr(args, "model", None)
    if model_name is None:
        return None
    return resolve_model_selection(
        config_path=Path(args.model_config),
        model_name=model_name,
    )


def _trace_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    trace_root = ROOT / "traces"
    target = (trace_root / path).resolve()
    if trace_root.resolve() not in target.parents and target != trace_root.resolve():
        raise ValueError("--trace relative path must stay under traces/")
    return target
