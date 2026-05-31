"""三層護欄 + 去重 (spec §3.2).

Layer 1  Horizon      — hard ceiling on hop_count, can never be skipped.
Layer 2  Claude self-decides whether to reply — lives in observatory (soft).
Layer 3  Rate limit   — per conversation_id, fixed window via an injectable clock.
Extra    Dedup        — idempotency by message_id.

Only the *hard* mechanisms (layers 1, 3, dedup) live here; they are pure
Redis+clock logic with no knowledge of Claude. Layer 2 is enforced in
``observatory.main`` because it depends on Claude's structured output.
"""
from __future__ import annotations

from dataclasses import dataclass

from .clock import Clock, SystemClock
from .envelope import Envelope


# --- Layer 1: Horizon -------------------------------------------------------
def horizon_reached(env: Envelope) -> bool:
    """``hop_count >= max_hops`` → the signal must die (spec §3.2 layer 1).

    Checked on *receipt* of a message (spec §5.2). Using ``>=`` means a message
    whose hop_count has reached the ceiling is dropped before it can spawn yet
    another reply — this is the off-by-one boundary the tests pin down.
    """
    return env.hop_count >= env.max_hops


# --- Layer 3: Rate limit ----------------------------------------------------
@dataclass
class RateLimiter:
    """Fixed-window counter, keyed by an *injectable* clock so tests don't wait.

    The window index is ``floor(now / window_seconds)``. Each conversation gets
    its own counter key per window; crossing a window boundary (advance the
    clock) naturally resets the count. Redis TTL on the key is only housekeeping
    — correctness comes from the clock-derived window index, not from TTL
    expiry, which keeps the behaviour deterministic under a ManualClock.
    """

    redis: "object"
    max_per_window: int
    window_seconds: int = 60
    clock: Clock = SystemClock()
    prefix: str = "aether:rate"

    def _key(self, conversation_id: str) -> str:
        window_index = int(self.clock.now() // self.window_seconds)
        return f"{self.prefix}:{conversation_id}:{window_index}"

    def exceeded(self, conversation_id: str) -> bool:
        """Count this attempt; return True if it pushes the window over the cap."""
        key = self._key(conversation_id)
        n = self.redis.incr(key)
        if n == 1:
            # Generous TTL purely so dead keys get reaped; 2x the window is plenty.
            self.redis.expire(key, self.window_seconds * 2)
        return n > self.max_per_window


# --- Extra: Dedup (idempotency) --------------------------------------------
@dataclass
class Dedup:
    """At-most-once *effect* by message_id (spec §3.2 附加).

    Semantics: ``already_seen`` is checked at the top of processing, and
    ``mark_done`` is written only *after* a message has been successfully
    processed. This is the crash-safe ordering — if a consumer dies mid-process
    (before ``mark_done`` and before XACK), the message stays pending and gets
    redelivered and reprocessed (scenario 6), instead of being lost to a dedup
    key that was claimed but never honoured. A genuine duplicate *delivery* of an
    already-completed message_id is still skipped (scenario 4).
    """

    redis: "object"
    ttl_seconds: int = 86400
    prefix: str = "aether:dedup"

    def _key(self, message_id: str) -> str:
        return f"{self.prefix}:{message_id}"

    def already_seen(self, message_id: str) -> bool:
        return self.redis.exists(self._key(message_id)) == 1

    def mark_done(self, message_id: str) -> None:
        self.redis.set(self._key(message_id), 1, ex=self.ttl_seconds)
