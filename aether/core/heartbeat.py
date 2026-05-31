"""Liveness via heartbeat keys (spec §4.3, §13.4).

Each Observatory periodically writes ``aether:heartbeat:<id>`` with a TTL, so
senders can check "is the target actually online" before sending a Comet, and
Stargazer can dim offline stars. The clock is injectable so tests are
deterministic; online-ness is simply "the key exists" (a target is offline iff
no live heartbeat), which keeps the offline scenario testable without waiting
for a real TTL to lapse.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .clock import Clock, SystemClock


@dataclass
class Heartbeat:
    redis: "object"
    ttl_seconds: int = 30
    clock: Clock = field(default_factory=SystemClock)
    prefix: str = "aether:heartbeat"

    def _key(self, project_id: str) -> str:
        return f"{self.prefix}:{project_id}"

    def beat(self, project_id: str) -> None:
        """Refresh this Body's heartbeat (call ~every 10s in production)."""
        self.redis.set(self._key(project_id), self.clock.now(), ex=self.ttl_seconds)

    def is_online(self, project_id: str) -> bool:
        return self.redis.exists(self._key(project_id)) == 1

    def go_offline(self, project_id: str) -> None:
        """Explicitly drop a heartbeat (used by tests to simulate offline)."""
        self.redis.delete(self._key(project_id))
