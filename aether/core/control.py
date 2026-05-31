"""Operator control state (spec §18.2).

The control plane's persisted state lives here as plain Redis keys so the
Observatory can *read* it (pause/kill checks) without importing the operator
panel, and the operator panel can *write* it. Keeping the primitives in ``core``
keeps the dependency direction clean (observatory → core; operator panel → core),
and the read-only Stargazer never touches any of this.

State per conversation lives in one key ``aether:control:conv:<cid>``:
    (absent)  → active
    "paused"  → Observatory holds inbound messages until resumed
    "killed"  → Observatory drops messages, extinguished with reason=operator_kill
Plus a per-project kill switch ``aether:control:project:<pid>``.
"""
from __future__ import annotations

from dataclasses import dataclass

CONTROL_PREFIX = "aether:control"
PAUSED = "paused"
KILLED = "killed"


def conv_key(conversation_id: str) -> str:
    return f"{CONTROL_PREFIX}:conv:{conversation_id}"


def project_kill_key(project_id: str) -> str:
    return f"{CONTROL_PREFIX}:project:{project_id}"


@dataclass
class ControlPlane:
    """Read + write access to operator control state. The Observatory is given
    one for READS only (it never calls the write methods); the operator panel
    uses the write methods. Both are just Redis ops — the privilege boundary is
    the panel's auth, not this object."""

    redis: "object"

    # ---- reads (Observatory) ----------------------------------------------
    def _conv_state(self, conversation_id: str):
        return self.redis.get(conv_key(conversation_id))

    def is_paused(self, conversation_id: str) -> bool:
        return self._conv_state(conversation_id) == PAUSED

    def is_killed(self, conversation_id: str) -> bool:
        return self._conv_state(conversation_id) == KILLED

    def is_project_killed(self, project_id: str) -> bool:
        return self.redis.exists(project_kill_key(project_id)) == 1

    # ---- writes (operator panel) ------------------------------------------
    def pause(self, conversation_id: str) -> None:
        self.redis.set(conv_key(conversation_id), PAUSED)

    def resume(self, conversation_id: str) -> None:
        # Only clears a pause; a killed conversation stays killed.
        if self.is_paused(conversation_id):
            self.redis.delete(conv_key(conversation_id))

    def kill(self, conversation_id: str) -> None:
        self.redis.set(conv_key(conversation_id), KILLED)

    def kill_project(self, project_id: str) -> None:
        self.redis.set(project_kill_key(project_id), "1")

    def clear_project_kill(self, project_id: str) -> None:
        self.redis.delete(project_kill_key(project_id))
