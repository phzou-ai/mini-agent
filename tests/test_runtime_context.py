from __future__ import annotations

from types import SimpleNamespace

from vermay.runtime_context import RuntimeContextProvider


class StaticContextSource:
    def __init__(self, content: str) -> None:
        self.content = content

    def context_text(self) -> str:
        return self.content


class StaticSkillStore:
    def retrieve(self, user_input: str, *, limit: int):
        _ = user_input, limit
        return [
            SimpleNamespace(
                name="runbook",
                version="1",
                description="A very long runbook.",
                content="s" * 200,
            )
        ]


class StaticMemoryStore:
    def retrieve(self, user_input: str, *, limit: int):
        _ = user_input, limit
        return [SimpleNamespace(id="memory-1", content="m" * 200)]


def test_runtime_context_provider_bounds_each_section_and_total_context():
    provider = RuntimeContextProvider(
        mcp_prompts=StaticContextSource("p" * 200),
        skills=StaticSkillStore(),
        memory=StaticMemoryStore(),
        mcp_resources=StaticContextSource("r" * 200),
        max_context_characters=150,
        max_section_characters=80,
    )

    messages = provider.context_messages("inspect the cluster")

    assert sum(len(str(message.content)) for message in messages) <= 150
    assert len(messages) == 2
    assert str(messages[0].content).endswith("[Context section truncated.]")
    assert str(messages[1].content).endswith("[Context section truncated.]")
