from __future__ import annotations

import json
from dataclasses import dataclass

from .storage import AgentStore, utc_now


@dataclass(frozen=True)
class MemoryItem:
    id: int
    content: str
    tags: list[str]
    enabled: bool
    source: str | None
    created_at: str
    updated_at: str


class SQLiteMemoryStore:
    def __init__(self, store: AgentStore) -> None:
        self.store = store

    def add(self, content: str, tags: list[str] | None = None, source: str = "cli") -> MemoryItem:
        normalized = content.strip()
        if not normalized:
            raise ValueError("memory content cannot be empty")
        now = utc_now()
        cursor = self.store.execute(
            """
            INSERT INTO memory_items(content, tags, enabled, source, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?, ?)
            """,
            (normalized, json.dumps(_normalize_tags(tags or []), ensure_ascii=False), source, now, now),
        )
        item_id = int(cursor.lastrowid)
        return self.get(item_id)

    def get(self, item_id: int) -> MemoryItem:
        rows = self.store.query(
            """
            SELECT id, content, tags, enabled, source, created_at, updated_at
            FROM memory_items
            WHERE id=?
            """,
            (item_id,),
        )
        if not rows:
            raise KeyError(f"unknown memory item: {item_id}")
        return _memory_item_from_row(rows[0])

    def list(self, *, enabled: bool | None = None) -> list[MemoryItem]:
        if enabled is None:
            rows = self.store.query(
                """
                SELECT id, content, tags, enabled, source, created_at, updated_at
                FROM memory_items
                ORDER BY id DESC
                """
            )
        else:
            rows = self.store.query(
                """
                SELECT id, content, tags, enabled, source, created_at, updated_at
                FROM memory_items
                WHERE enabled=?
                ORDER BY id DESC
                """,
                (1 if enabled else 0,),
            )
        return [_memory_item_from_row(row) for row in rows]

    def disable(self, item_id: int) -> MemoryItem:
        existing = self.get(item_id)
        self.store.execute(
            """
            UPDATE memory_items
            SET enabled=0, updated_at=?
            WHERE id=?
            """,
            (utc_now(), existing.id),
        )
        return self.get(existing.id)

    def retrieve(self, query: str, *, tags: list[str] | None = None, limit: int = 5) -> list[MemoryItem]:
        candidates = self.list(enabled=True)
        if not candidates or limit <= 0:
            return []

        query_terms = _terms(query)
        tag_filter = set(_normalize_tags(tags or []))
        scored: list[tuple[int, MemoryItem]] = []
        for item in candidates:
            item_tags = set(item.tags)
            if tag_filter and not (item_tags & tag_filter):
                continue
            score = 0
            content = item.content.lower()
            for term in query_terms:
                if term in content:
                    score += 2
                if term in item_tags:
                    score += 3
            if not query_terms:
                score = 1
            scored.append((score, item))

        matched = [(score, item) for score, item in scored if score > 0]
        if not matched and not tag_filter:
            matched = [(1, item) for item in candidates[:limit]]
        matched.sort(key=lambda pair: (pair[0], pair[1].id), reverse=True)
        return [item for _, item in matched[:limit]]


def _memory_item_from_row(row) -> MemoryItem:
    try:
        tags = json.loads(row["tags"])
    except json.JSONDecodeError:
        tags = []
    if not isinstance(tags, list):
        tags = []
    return MemoryItem(
        id=int(row["id"]),
        content=str(row["content"]),
        tags=[str(tag) for tag in tags],
        enabled=bool(row["enabled"]),
        source=row["source"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized = []
    for tag in tags:
        value = tag.strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _terms(value: str) -> set[str]:
    return {part.strip(".,:;()[]{}'\"`").lower() for part in value.split() if part.strip()}
