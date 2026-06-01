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
from redis.exceptions import WatchError

REGISTRY_KEY = "aether:registry"


class DuplicateBodyError(Exception):
    """A body id is already registered with DIFFERENT content (e.g. another host
    claimed the same project_id). Fail-closed: two bodies sharing an id would also
    share ``aether:inbox:<id>`` / ``grp-<id>`` → split-brain message theft."""

    def __init__(self, project_id: str, existing: str, incoming: str) -> None:
        self.project_id = project_id
        self.existing = existing
        self.incoming = incoming
        super().__init__(
            f"body '{project_id}' is already registered with different content. "
            f"Use --force to overwrite, or pick a different id "
            f"(two hosts must not register the same body id)."
        )


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

    def register_body(self, body: Body, *, force: bool = False, _max_retries: int = 50) -> str:
        """Atomically register one body (compare-and-set via WATCH/MULTI).

        Returns "added" | "unchanged" | "forced". Raises ``DuplicateBodyError``
        when the id exists with different content and ``force`` is False. The CAS
        loop closes the race where two hosts both read "absent" and last-write-wins.
        """
        new_json = body.to_json()
        for _ in range(_max_retries):
            with self.redis.pipeline() as pipe:
                try:
                    pipe.watch(REGISTRY_KEY)
                    existing = pipe.hget(REGISTRY_KEY, body.project_id)  # immediate (watch mode)
                    if existing == new_json:
                        pipe.unwatch()
                        return "unchanged"
                    if existing is not None and not force:
                        pipe.unwatch()
                        raise DuplicateBodyError(body.project_id, existing, new_json)
                    pipe.multi()
                    pipe.hset(REGISTRY_KEY, body.project_id, new_json)
                    pipe.execute()  # raises WatchError if REGISTRY_KEY changed since watch
                    return "added" if existing is None else "forced"
                except WatchError:
                    continue  # someone wrote between our read and exec → retry
        raise RuntimeError(f"register_body: too much contention on {REGISTRY_KEY}")

    def sync(self, bodies: Dict[str, Body], *, prune: bool = False, force: bool = False) -> None:
        """Publish bodies into the registry.

        Default (``prune=False``) is ADDITIVE: each body is registered via the
        atomic CAS, so syncing host B's bodies never deletes host A/C's (the
        old delete-all-then-add wiped the whole table). ``prune=True`` restores
        the destructive full-replace for single-owner seed/admin use (demos)."""
        if prune:
            self.redis.delete(REGISTRY_KEY)
            if bodies:
                self.redis.hset(REGISTRY_KEY, mapping={pid: b.to_json() for pid, b in bodies.items()})
            return
        for b in bodies.values():
            self.register_body(b, force=force)

    def load_and_sync(self, path: str, *, prune: bool = False, force: bool = False) -> Dict[str, Body]:
        bodies = load_constellation(path)
        self.sync(bodies, prune=prune, force=force)
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
