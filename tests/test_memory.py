from __future__ import annotations

from langchain_core.messages import SystemMessage

from vermay.memory import SQLiteMemoryStore
from vermay.runtime_context import RuntimeContextProvider
from vermay.storage import AgentStore


def test_memory_add_list_disable_and_retrieve(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    memory = SQLiteMemoryStore(store)

    item = memory.add("Prefer read-only kubernetes inspection first.", tags=["k8s", "preference"])

    assert item.id == 1
    assert memory.list()[0].content == "Prefer read-only kubernetes inspection first."
    assert memory.retrieve("check k8s status")[0].id == item.id
    disabled = memory.disable(item.id)
    assert disabled.enabled is False
    assert memory.retrieve("check k8s status") == []
    store.close()


def test_runtime_context_injects_enabled_memory(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    memory = SQLiteMemoryStore(store)
    memory.add("Shanghai weather output should include wind.", tags=["weather"])

    messages = RuntimeContextProvider(memory=memory).context_messages("weather forecast for Shanghai")

    assert len(messages) == 1
    assert isinstance(messages[0], SystemMessage)
    assert "Memory:" in str(messages[0].content)
    assert "Shanghai weather" in str(messages[0].content)
    store.close()
