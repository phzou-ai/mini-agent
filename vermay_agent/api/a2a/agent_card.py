from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class A2AAgentSkillConfig:
    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class A2AAgentCardConfig:
    name: str = "Vermay Agent"
    description: str = "An A2A-first main agent for direct answers, local task execution, and child-agent delegation."
    url: str = "http://127.0.0.1:8000/rpc"
    version: str = "0.1.0"
    protocol_versions: tuple[str, ...] = ("0.3",)
    default_input_modes: tuple[str, ...] = ("text/plain",)
    default_output_modes: tuple[str, ...] = ("text/plain",)
    streaming: bool = False
    push_notifications: bool = False
    extended_agent_card: bool = False
    skills: tuple[A2AAgentSkillConfig, ...] = field(
        default_factory=lambda: (
            A2AAgentSkillConfig(
                id="direct-answer",
                name="Direct answer",
                description="Answer lightweight requests directly without creating a task.",
                tags=("agent", "message", "local_message"),
                examples=("Summarize the current session state.",),
            ),
            A2AAgentSkillConfig(
                id="local-task-execution",
                name="Local task execution",
                description="Run a local agent task and return task status plus artifacts.",
                tags=("agent", "task", "tools", "approval", "local_task"),
                examples=("Inspect why the latest tool call failed.",),
            ),
            A2AAgentSkillConfig(
                id="child-agent-delegation",
                name="Child-agent delegation",
                description="Route suitable requests to registered child A2A agents.",
                tags=("agent", "routing", "delegation", "remote_agent"),
                examples=("Use the registered SQL agent to inspect SQLite trace events.",),
            ),
        )
    )
    security_schemes: dict[str, Any] = field(default_factory=dict)
    security: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(
        default_factory=lambda: {
            "routeKinds": ["local_message", "local_task", "remote_agent"],
            "executionModes": ["message", "task", "auto"],
        }
    )


def build_agent_card(config: A2AAgentCardConfig | None = None) -> dict[str, Any]:
    active = config or A2AAgentCardConfig()
    return {
        "name": active.name,
        "description": active.description,
        "url": active.url,
        "preferredTransport": "JSONRPC",
        "version": active.version,
        "protocolVersions": list(active.protocol_versions),
        "capabilities": {
            "streaming": active.streaming,
            "pushNotifications": active.push_notifications,
            "extendedAgentCard": active.extended_agent_card,
        },
        "defaultInputModes": list(active.default_input_modes),
        "defaultOutputModes": list(active.default_output_modes),
        "skills": [_skill_payload(skill) for skill in active.skills],
        "securitySchemes": dict(active.security_schemes),
        "security": list(active.security),
        "metadata": dict(active.metadata),
    }


def _skill_payload(skill: A2AAgentSkillConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
    }
    if skill.tags:
        payload["tags"] = list(skill.tags)
    if skill.examples:
        payload["examples"] = list(skill.examples)
    return payload
