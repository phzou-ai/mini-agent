from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..app_factory import (
    DEFAULT_AGENT_STORE_PATH,
    DEFAULT_MCP_CONFIG_PATH,
    DEFAULT_SKILL_PROPOSALS_PATH,
    DEFAULT_SKILLS_PATH,
    ROOT,
)
from ..evaluation import OfflineReplayService
from ..mcp.client import MCPClientManager, load_mcp_server_configs
from ..memory import SQLiteMemoryStore
from ..skills import SkillStore
from ..storage import AgentStore


def run_subcommand(argv: list[str]) -> None:
    command = argv[0]
    if command == "memory":
        run_memory_command(argv[1:])
        return
    if command == "skills":
        run_skills_command(argv[1:])
        return
    if command == "eval":
        run_eval_command(argv[1:])
        return
    if command == "mcp":
        run_mcp_command(argv[1:])
        return
    if command == "serve":
        run_serve_command(argv[1:])
        return
    raise SystemExit(f"unknown command: {command}")


def run_serve_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="vermay serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    import uvicorn
    from ..api.app import create_app

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
    )


def run_memory_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="vermay memory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("content")
    add_parser.add_argument("--tag", action="append", default=[])

    subparsers.add_parser("list")

    disable_parser = subparsers.add_parser("disable")
    disable_parser.add_argument("id", type=int)

    args = parser.parse_args(argv)
    store = AgentStore(DEFAULT_AGENT_STORE_PATH)
    try:
        memory = SQLiteMemoryStore(store)
        if args.command == "add":
            item = memory.add(args.content, tags=args.tag)
            print(f"added memory {item.id}")
        elif args.command == "list":
            for item in memory.list():
                status = "enabled" if item.enabled else "disabled"
                tags = ",".join(item.tags) if item.tags else "-"
                print(f"{item.id}\t{status}\t{tags}\t{item.content}")
        elif args.command == "disable":
            item = memory.disable(args.id)
            print(f"disabled memory {item.id}")
    finally:
        store.close()


def run_skills_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="vermay skills")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list")
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("name")
    propose_parser = subparsers.add_parser("propose-from-trace")
    propose_parser.add_argument("--trace", required=True)
    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("proposal_id")

    args = parser.parse_args(argv)
    store = AgentStore(DEFAULT_AGENT_STORE_PATH)
    try:
        skills = SkillStore(
            authored_dir=DEFAULT_SKILLS_PATH,
            proposals_dir=DEFAULT_SKILL_PROPOSALS_PATH,
            store=store,
        )
        if args.command == "list":
            for skill in skills.list_skills():
                triggers = ",".join(skill.triggers) if skill.triggers else "-"
                print(f"{skill.name}\t{skill.version}\t{triggers}\t{skill.description}")
        elif args.command == "show":
            skill = skills.show(args.name)
            print(skill.path.read_text(encoding="utf-8"), end="")
        elif args.command == "propose-from-trace":
            path = skills.propose_from_trace(Path(args.trace))
            print(f"created proposal {path.stem}")
        elif args.command == "approve":
            skill = skills.approve(args.proposal_id)
            print(f"approved skill {skill.name}")
    finally:
        store.close()


def run_eval_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="vermay eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay_parser = subparsers.add_parser("replay")
    replay_source = replay_parser.add_mutually_exclusive_group(required=True)
    replay_source.add_argument("--trace")
    replay_source.add_argument("--scenario")
    subparsers.add_parser("list-runs")

    args = parser.parse_args(argv)
    store = AgentStore(DEFAULT_AGENT_STORE_PATH)
    try:
        service = OfflineReplayService(store=store, report_dir=ROOT / "data" / "eval_runs")
        if args.command == "replay":
            if args.trace:
                report = service.replay_trace(Path(args.trace))
            else:
                report = service.replay_scenario(Path(args.scenario))
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        elif args.command == "list-runs":
            for run in service.list_runs():
                print(f"{run['id']}\t{run['status']}\t{run['source_type']}\t{run['source_path']}")
    finally:
        store.close()


def run_mcp_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="vermay mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default=str(DEFAULT_MCP_CONFIG_PATH))

    subparsers.add_parser("list-servers", parents=[config_parent])

    list_tools_parser = subparsers.add_parser("list-tools", parents=[config_parent])
    list_tools_parser.add_argument("--server", default=None, help="Configured MCP server name to inspect")

    list_resources_parser = subparsers.add_parser("list-resources", parents=[config_parent])
    list_resources_parser.add_argument("--server", default=None, help="Configured MCP server name to inspect")

    list_prompts_parser = subparsers.add_parser("list-prompts", parents=[config_parent])
    list_prompts_parser.add_argument("--server", default=None, help="Configured MCP server name to inspect")

    args = parser.parse_args(argv)
    config_path = Path(args.config)

    if args.command == "list-servers":
        for server in load_mcp_server_configs(config_path):
            print(
                "\t".join(
                    [
                        f"name={server.name}",
                        f"transport={server.transport}",
                        f"tool_exposure={server.tool_exposure}",
                        f"read_only={server.read_only}",
                    ]
                )
            )
    elif args.command == "list-tools":
        reports = MCPClientManager(config_path).list_tool_reports(server_name=args.server)
        for report in reports:
            print(
                "\t".join(
                    [
                        f"server={report.server}",
                        f"original_name={report.original_name}",
                        f"model_facing_name={report.model_facing_name}",
                        f"read_only={report.read_only}",
                        f"exposed_by_policy={report.exposed_by_policy}",
                        f"requires_approval={report.requires_approval}",
                        f"description={report.description}",
                    ]
                )
            )
    elif args.command == "list-resources":
        resources = MCPClientManager(config_path).list_resources(server_name=args.server)
        for resource in resources:
            print(
                "\t".join(
                    [
                        f"server={resource.server.name}",
                        f"uri={resource.uri}",
                        f"name={resource.name}",
                        f"title={resource.title or ''}",
                        f"mime_type={resource.mime_type or ''}",
                        f"size={resource.size if resource.size is not None else ''}",
                        f"template={resource.is_template}",
                        f"description={resource.description}",
                    ]
                )
            )
    elif args.command == "list-prompts":
        prompts = MCPClientManager(config_path).list_prompts(server_name=args.server)
        for prompt in prompts:
            arguments = ",".join(argument["name"] for argument in prompt.arguments if argument.get("name"))
            print(
                "\t".join(
                    [
                        f"server={prompt.server.name}",
                        f"name={prompt.name}",
                        f"title={prompt.title or ''}",
                        f"arguments={arguments}",
                        f"description={prompt.description}",
                    ]
                )
            )
