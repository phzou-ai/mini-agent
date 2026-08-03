from __future__ import annotations

from dataclasses import dataclass, replace

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from .models import MessageRecord, MessageRole
from .store import MainAgentStore


@dataclass(frozen=True)
class ContextAssemblyPolicy:
    """Bounded persisted-message history for one model-facing execution path.

    The current user input is always retained verbatim. Character budgets only
    apply to preceding history so an accepted task request is never silently
    changed while preparing its prompt.
    """

    max_messages: int
    max_history_characters: int
    max_message_characters: int

    def __post_init__(self) -> None:
        if self.max_messages <= 0:
            raise ValueError("max_messages must be positive")
        if self.max_history_characters < 0:
            raise ValueError("max_history_characters must be non-negative")
        if self.max_message_characters <= 0:
            raise ValueError("max_message_characters must be positive")


# These are character caps rather than a model-token guarantee. They make the
# current single-host runtime deterministic while leaving token-aware summaries
# as a later capability.
ROUTER_CONTEXT_POLICY = ContextAssemblyPolicy(
    max_messages=8,
    max_history_characters=6_000,
    max_message_characters=1_500,
)
DIRECT_MESSAGE_CONTEXT_POLICY = ContextAssemblyPolicy(
    max_messages=12,
    max_history_characters=14_000,
    max_message_characters=4_000,
)
LOCAL_TASK_CONTEXT_POLICY = ContextAssemblyPolicy(
    max_messages=16,
    max_history_characters=18_000,
    max_message_characters=5_000,
)

_TRUNCATION_SUFFIX = "\n[Earlier content truncated for context.]"


def recent_messages(store: MainAgentStore, context_id: str, *, limit: int = 10) -> list[MessageRecord]:
    if limit <= 0:
        return []
    return store.list_context_messages(context_id, limit=limit)


def router_context_through_input(
    store: MainAgentStore,
    context_id: str,
    input_message_id: str,
) -> list[MessageRecord]:
    return _context_through_input(
        store,
        context_id,
        input_message_id,
        policy=ROUTER_CONTEXT_POLICY,
    )


def direct_message_context_through_input(
    store: MainAgentStore,
    context_id: str,
    input_message_id: str,
) -> list[MessageRecord]:
    return _context_through_input(
        store,
        context_id,
        input_message_id,
        policy=DIRECT_MESSAGE_CONTEXT_POLICY,
    )


def local_task_context(store: MainAgentStore, task_id: str) -> list[MessageRecord]:
    """Return the task's durable initial input cut under the local-task policy."""

    task = store.get_task(task_id)
    if task is None:
        raise ValueError(f"unknown task: {task_id}")
    if task.input_context_sequence <= 0:
        raise RuntimeError(f"task input cut is unavailable: {task_id}")
    messages = store.list_context_messages(
        task.context_id,
        limit=LOCAL_TASK_CONTEXT_POLICY.max_messages,
        through_sequence=task.input_context_sequence,
    )
    return _bound_history(
        messages,
        protected_message_id=task.input_message_id,
        policy=LOCAL_TASK_CONTEXT_POLICY,
    )


def to_langchain_message(message: MessageRecord) -> BaseMessage:
    text = text_from_parts(message.parts)
    if message.role == MessageRole.SYSTEM:
        return SystemMessage(content=text)
    if message.role == MessageRole.AGENT:
        return AIMessage(content=text)
    return HumanMessage(content=text)


def text_from_parts(parts: list[dict]) -> str:
    return "\n".join(
        str(part.get("text", "")).strip()
        for part in parts
        if isinstance(part.get("text"), str)
    ).strip()


def _context_through_input(
    store: MainAgentStore,
    context_id: str,
    input_message_id: str,
    *,
    policy: ContextAssemblyPolicy,
) -> list[MessageRecord]:
    input_message = store.get_message(input_message_id)
    if input_message is None:
        raise ValueError(f"unknown input message: {input_message_id}")
    if input_message.context_id != context_id:
        raise ValueError(f"input message context mismatch: {input_message_id}")
    messages = store.list_context_messages(
        context_id,
        limit=policy.max_messages,
        through_sequence=input_message.context_sequence,
    )
    return _bound_history(messages, protected_message_id=input_message_id, policy=policy)


def _bound_history(
    messages: list[MessageRecord],
    *,
    protected_message_id: str,
    policy: ContextAssemblyPolicy,
) -> list[MessageRecord]:
    """Keep newest preceding messages within a bounded character window."""

    selected: list[MessageRecord] = []
    remaining = policy.max_history_characters
    for message in reversed(messages):
        if message.message_id == protected_message_id:
            selected.append(message)
            continue

        text = text_from_parts(message.parts)
        if not text or remaining <= 0:
            continue
        allowed = min(policy.max_message_characters, remaining)
        bounded_text = _truncate_for_context(text, allowed)
        if not bounded_text:
            continue
        selected.append(_message_with_text(message, bounded_text))
        remaining -= len(bounded_text)
    return list(reversed(selected))


def _message_with_text(message: MessageRecord, text: str) -> MessageRecord:
    if text == text_from_parts(message.parts):
        return message
    return replace(message, parts=[{"kind": "text", "text": text}])


def _truncate_for_context(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_SUFFIX):
        return text[:limit]
    return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
