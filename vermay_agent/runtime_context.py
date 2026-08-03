from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import BaseMessage, SystemMessage

from .memory import SQLiteMemoryStore
from .mcp.prompts import MCPPromptProvider
from .mcp.resources import MCPResourceProvider
from .skills import SkillStore


@dataclass
class RuntimeContextProvider:
    mcp_prompts: MCPPromptProvider | None = None
    memory: SQLiteMemoryStore | None = None
    skills: SkillStore | None = None
    mcp_resources: MCPResourceProvider | None = None
    memory_limit: int = 5
    skill_limit: int = 3
    max_context_characters: int = 16_000
    max_section_characters: int = 5_000

    def __post_init__(self) -> None:
        if self.memory_limit < 0:
            raise ValueError("memory_limit must be non-negative")
        if self.skill_limit < 0:
            raise ValueError("skill_limit must be non-negative")
        if self.max_context_characters <= 0:
            raise ValueError("max_context_characters must be positive")
        if self.max_section_characters <= 0:
            raise ValueError("max_section_characters must be positive")

    def context_messages(self, user_input: str) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        remaining = self.max_context_characters

        def append_section(content: str) -> None:
            nonlocal remaining
            if not content or remaining <= 0:
                return
            allowed = min(self.max_section_characters, remaining)
            bounded = _truncate_context_section(content, allowed)
            if not bounded:
                return
            messages.append(SystemMessage(content=bounded))
            remaining -= len(bounded)

        if self.mcp_prompts is not None:
            content = self.mcp_prompts.context_text()
            append_section(content)

        if self.skills is not None:
            skills = self.skills.retrieve(user_input, limit=self.skill_limit)
            if skills:
                sections = []
                for skill in skills:
                    sections.append(
                        "\n".join(
                            [
                                f"## {skill.name}",
                                f"version: {skill.version}",
                                f"description: {skill.description}",
                                "",
                                skill.content,
                            ]
                        )
                )
                append_section("Relevant skills:\n\n" + "\n\n".join(sections))
        if self.memory is not None:
            memory_items = self.memory.retrieve(user_input, limit=self.memory_limit)
            if memory_items:
                content = "\n".join(f"- [{item.id}] {item.content}" for item in memory_items)
                append_section(f"Memory:\n{content}")

        if self.mcp_resources is not None:
            content = self.mcp_resources.context_text()
            append_section(content)
        return messages


_TRUNCATION_SUFFIX = "\n[Context section truncated.]"


def _truncate_context_section(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    if limit <= len(_TRUNCATION_SUFFIX):
        return content[:limit]
    return content[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
