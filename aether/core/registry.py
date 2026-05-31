"""Constellation registry (spec §4.1, §13.4).

``constellation.yaml`` is the source of truth; on startup it is loaded into
``aether:registry`` (a Redis hash) for runtime lookup. The registry answers two
questions the router needs: "who exists / what can they do" (for injecting into
the prompt so Claude self-selects a recipient) and "is this chosen recipient a
real, known Body" (validation before sending).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional

import yaml

REGISTRY_KEY = "aether:registry"


@dataclass
class Body:
    project_id: str
    description: str
    capabilities: list
    inbox: str
    working_dir: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps({
            "description": self.description,
            "capabilities": self.capabilities,
            "inbox": self.inbox,
            "working_dir": self.working_dir,
        }, ensure_ascii=False)

    @staticmethod
    def from_json(project_id: str, s: str) -> "Body":
        d = json.loads(s)
        return Body(project_id=project_id, description=d.get("description", ""),
                    capabilities=d.get("capabilities", []), inbox=d.get("inbox", ""),
                    working_dir=d.get("working_dir"))


def load_constellation(path: str) -> Dict[str, Body]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    bodies = {}
    for pid, meta in (data.get("bodies") or {}).items():
        bodies[pid] = Body(
            project_id=pid,
            description=meta.get("description", ""),
            capabilities=meta.get("capabilities", []),
            inbox=meta.get("inbox", f"aether:inbox:{pid}"),
            working_dir=meta.get("working_dir"),
        )
    return bodies


class Registry:
    """Runtime view backed by ``aether:registry``."""

    def __init__(self, redis: "object") -> None:
        self.redis = redis

    def sync(self, bodies: Dict[str, Body]) -> None:
        self.redis.delete(REGISTRY_KEY)
        if bodies:
            self.redis.hset(REGISTRY_KEY, mapping={pid: b.to_json() for pid, b in bodies.items()})

    def load_and_sync(self, path: str) -> Dict[str, Body]:
        bodies = load_constellation(path)
        self.sync(bodies)
        return bodies

    def all(self) -> Dict[str, Body]:
        raw = self.redis.hgetall(REGISTRY_KEY)
        return {pid: Body.from_json(pid, s) for pid, s in raw.items()}

    def has(self, project_id: str) -> bool:
        return self.redis.hexists(REGISTRY_KEY, project_id)

    def get(self, project_id: str) -> Optional[Body]:
        s = self.redis.hget(REGISTRY_KEY, project_id)
        return Body.from_json(project_id, s) if s else None

    def add(self, body: Body) -> None:
        """Register a single body without disturbing the rest (e.g. a transient
        interactive consult identity). Unlike ``sync``, does not replace the table."""
        self.redis.hset(REGISTRY_KEY, body.project_id, body.to_json())

    def remove(self, project_id: str) -> None:
        self.redis.hdel(REGISTRY_KEY, project_id)
