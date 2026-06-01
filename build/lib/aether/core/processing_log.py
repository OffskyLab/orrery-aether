"""Per-message processing log — the idempotency state machine (spec §13.1).

Phase 1's "mark done after success" flag is no longer enough: once we really
call ``claude -p`` and really emit reply Comets, a crash + redelivery must NOT
re-pay for Claude or re-send a reply. So each inbound message_id gets a small
persisted state machine:

    RECEIVED → CLAUDE_DONE → REPLY_EMITTED → ACKED

with Claude's result and the derived reply_message_id stored alongside, so a
redelivery resumes *from the last durable state* instead of redoing everything.
This is a lightweight transactional-outbox / processing-log pattern.

Stored in a Redis hash with TTL; no Claude knowledge here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

RECEIVED = "RECEIVED"
CLAUDE_DONE = "CLAUDE_DONE"
REPLY_EMITTED = "REPLY_EMITTED"
ACKED = "ACKED"

# Monotonic ordering so the pipeline can ask "have we passed state X yet?".
ORDER = {RECEIVED: 0, CLAUDE_DONE: 1, REPLY_EMITTED: 2, ACKED: 3}


@dataclass
class ProcessingLog:
    redis: "object"
    ttl_seconds: int = 86400
    prefix: str = "aether:proclog"

    def _key(self, message_id: str) -> str:
        return f"{self.prefix}:{message_id}"

    # ---- state ------------------------------------------------------------
    def state(self, message_id: str) -> Optional[str]:
        return self.redis.hget(self._key(message_id), "state")

    def at_least(self, message_id: str, state: str) -> bool:
        cur = self.state(message_id)
        return cur is not None and ORDER[cur] >= ORDER[state]

    def mark(self, message_id: str, state: str, **fields) -> None:
        key = self._key(message_id)
        payload = {"state": state}
        payload.update({k: v for k, v in fields.items() if v is not None})
        self.redis.hset(key, mapping=payload)
        self.redis.expire(key, self.ttl_seconds)

    # ---- Claude result bookkeeping ---------------------------------------
    def save_claude_result(self, message_id: str, result: dict,
                           session_id: Optional[str] = None,
                           reply_message_id: Optional[str] = None) -> None:
        """Persist the parsed control result so a redelivery after CLAUDE_DONE
        can reply WITHOUT calling Claude again."""
        self.mark(
            message_id, CLAUDE_DONE,
            claude_result=json.dumps(result, ensure_ascii=False),
            session_id=session_id,
            reply_message_id=reply_message_id,
        )

    def load_claude_result(self, message_id: str) -> Optional[dict]:
        raw = self.redis.hget(self._key(message_id), "claude_result")
        return json.loads(raw) if raw else None

    def get(self, message_id: str) -> dict:
        return self.redis.hgetall(self._key(message_id))
