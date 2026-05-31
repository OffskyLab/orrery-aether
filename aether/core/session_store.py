"""conversation_id → local claude session_id mapping (spec §6, §13.3).

Session ids are LOCAL to each Body and must never travel on the bus; each
Observatory keeps its own map. Persisted in a project-scoped Redis hash so a
restart can still ``--resume`` an in-flight conversation. (Spec allows SQLite or
a Redis hash; we use Redis to stay consistent with the rest of the transport.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionStore:
    redis: "object"
    project_id: str
    prefix: str = "aether:sessions"

    def _key(self) -> str:
        return f"{self.prefix}:{self.project_id}"

    def get(self, conversation_id: str) -> Optional[str]:
        return self.redis.hget(self._key(), conversation_id)

    def set(self, conversation_id: str, session_id: str) -> None:
        self.redis.hset(self._key(), conversation_id, session_id)

    def forget(self, conversation_id: str) -> None:
        """Drop a session that turned out to be unresumable (spec §13.3:
        fall back to a fresh session, never hard-crash)."""
        self.redis.hdel(self._key(), conversation_id)


class InMemorySessionStore:
    """Phase-1-compatible fallback when no persistence is wired."""

    def __init__(self) -> None:
        self._m: dict = {}

    def get(self, conversation_id: str) -> Optional[str]:
        return self._m.get(conversation_id)

    def set(self, conversation_id: str, session_id: str) -> None:
        self._m[conversation_id] = session_id

    def forget(self, conversation_id: str) -> None:
        self._m.pop(conversation_id, None)
