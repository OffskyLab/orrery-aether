"""AetherClient — the Redis Streams transport (spec §2).

Responsibilities: emit messages to the right stream, mirror every message to the
global ``aether:events`` stream (so Stargazer / the tests can rebuild history),
and the consumer-group plumbing (read / ack / claim-pending) that gives reliable
delivery. No Claude knowledge here either.

Every ``aether:events`` record carries the §15.1 contract — ``event_type``
(message | done | terminated | progress), ``ts`` (ISO 8601) and a top-level
``conversation_id`` — so Stargazer can render reliably. The detailed internal
``kind`` and all prior fields are kept too, so Phase 1/2 consumers are unaffected.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import redis as redis_lib

from .envelope import BROADCAST, Envelope

EVENTS_STREAM = "aether:events"
BROADCAST_STREAM = "aether:broadcast"

# Internal kind → §15.1 event_type. Anything that stops/defers a message (rate,
# dedup, malformed, offline, invalid recipient) is grouped as "terminated" — that
# is exactly the set Stargazer's 熄滅紀錄 (Terminated Log, §15.4) surfaces.
EVENT_TYPE_BY_KIND = {
    "message": "message",
    "processing_done": "done",
    "terminated": "terminated",
    "duplicate_skipped": "terminated",
    "held": "terminated",
    "reply_rejected": "terminated",
    "malformed_output": "terminated",
    "ack_suppressed": "terminated",  # §17.1 register gate (anti-pleasantry)
    "operator_action": "operator_action",  # §18.2 operator-panel audit
    "progress": "progress",
    "processing_start": "progress",
}

# §15.5/§15.6-2: cap aether:events growth so the stream + dashboard aren't dragged
# down by unbounded history (approximate trim is cheap).
DEFAULT_EVENTS_MAXLEN = 50_000


def inbox_stream(project_id: str) -> str:
    return f"aether:inbox:{project_id}"


def make_redis(host: str = "localhost", port: int = 6379, db: int = 0, *,
               password: Optional[str] = None, username: Optional[str] = None,
               ssl: bool = False, ssl_ca_certs: Optional[str] = None,
               ssl_certfile: Optional[str] = None,
               ssl_keyfile: Optional[str] = None) -> "redis_lib.Redis":
    """Build a Redis client decoded to ``str`` so callers never juggle bytes.

    The new keyword-only auth/TLS params default to None/False, so a bare
    ``make_redis()`` is byte-identical to before (cross-machine spec invariant):
    only host/port/db/decode_responses reach ``redis.Redis``. A value is passed
    through ONLY when set, so we never change AUTH/TLS behaviour by accident.
    """
    kwargs: dict = {"host": host, "port": port, "db": db, "decode_responses": True}
    if password is not None:
        kwargs["password"] = password
    if username is not None:
        kwargs["username"] = username
    if ssl:
        kwargs["ssl"] = True
        if ssl_ca_certs is not None:
            kwargs["ssl_ca_certs"] = ssl_ca_certs
        if ssl_certfile is not None:
            kwargs["ssl_certfile"] = ssl_certfile
        if ssl_keyfile is not None:
            kwargs["ssl_keyfile"] = ssl_keyfile
    return redis_lib.Redis(**kwargs)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AetherClient:
    def __init__(self, redis: "redis_lib.Redis",
                 events_maxlen: Optional[int] = DEFAULT_EVENTS_MAXLEN) -> None:
        self.r = redis
        self.events_maxlen = events_maxlen

    # ---- producing ---------------------------------------------------------
    def emit(self, env: Envelope) -> str:
        """Route an envelope to its stream AND mirror it to aether:events.

        Comet → ``aether:inbox:<to>``; Wave → ``aether:broadcast`` (spec §3.3).
        Returns the destination stream entry id.
        """
        env.validate()
        payload = {"data": env.to_json()}
        if env.to == BROADCAST:
            stream = BROADCAST_STREAM
        else:
            stream = inbox_stream(env.to)
        entry_id = self.r.xadd(stream, payload)
        # Mirror to the global event stream (spec §2.3). Tag the kind so readers
        # can tell a real message apart from a lifecycle/terminated marker.
        self._mirror({"kind": "message", "envelope": env.to_dict()})
        return entry_id

    def emit_event(
        self,
        kind: str,
        env: Optional[Envelope] = None,
        reason: Optional[str] = None,
        **extra: Any,
    ) -> str:
        """Write a lifecycle marker to aether:events only (not to any inbox).

        Used for ``terminated`` (horizon / rate_limited), ``processing_start``,
        ``processing_done`` — the data Stargazer's Terminated Log / Timeline read.
        """
        record: dict = {"kind": kind}
        if reason is not None:
            record["reason"] = reason
        if env is not None:
            record["envelope"] = env.to_dict()
            # Hoist the routing fields so the Timeline can read every hop's
            # from→to→hop_count without re-parsing the nested envelope.
            record["from"] = env.from_
            record["to"] = env.to
            record["hop_count"] = env.hop_count
            record["conversation_id"] = env.conversation_id
            record["message_id"] = env.message_id
        record.update(extra)
        return self._mirror(record)

    def emit_progress(self, conversation_id: str, project_id: str, kind: str,
                      **detail: Any) -> str:
        """Write a live-telescope milestone (spec §15.3). ``kind`` is one of
        turn_start | tool_use | turn_done."""
        return self._mirror({
            "kind": "progress",
            "conversation_id": conversation_id,
            "project_id": project_id,
            "progress": {"kind": kind, **detail},
        })

    def emit_operator_action(self, actor: str, action: str,
                             conversation_id: Optional[str] = None, **detail: Any) -> str:
        """Audit one operator-panel action (spec §18.2 全程稽核). Written as
        ``event_type=operator_action`` with actor + timestamp, so every privileged
        write is itself observable on Stargazer's timeline and reconstructable."""
        record: dict = {"kind": "operator_action", "actor": actor, "action": action}
        if conversation_id is not None:
            record["conversation_id"] = conversation_id
        record.update(detail)
        return self._mirror(record)

    def _finalize(self, record: dict) -> dict:
        """Stamp the §15.1 contract onto every event record."""
        record.setdefault("event_type",
                          EVENT_TYPE_BY_KIND.get(record.get("kind"), record.get("kind")))
        record.setdefault("ts", _now_iso())
        if "conversation_id" not in record:
            env = record.get("envelope")
            if env and env.get("conversation_id"):
                record["conversation_id"] = env["conversation_id"]
        return record

    def _mirror(self, record: dict) -> str:
        self._finalize(record)
        payload = {"data": json.dumps(record, ensure_ascii=False)}
        if self.events_maxlen:
            return self.r.xadd(EVENTS_STREAM, payload,
                               maxlen=self.events_maxlen, approximate=True)
        return self.r.xadd(EVENTS_STREAM, payload)

    # ---- consumer groups ---------------------------------------------------
    def ensure_group(self, stream: str, group: str) -> None:
        """Idempotently create a consumer group, creating the stream if needed."""
        try:
            self.r.xgroup_create(stream, group, id="0", mkstream=True)
        except redis_lib.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def read_group(
        self,
        group: str,
        consumer: str,
        streams: dict,
        count: int = 10,
        block_ms: int = 0,
    ) -> list:
        """XREADGROUP wrapper. Returns ``[(stream, entry_id, env), ...]`` of new
        (never-delivered) messages, with the envelope already parsed."""
        resp = self.r.xreadgroup(group, consumer, streams, count=count, block=block_ms)
        out = []
        for stream, entries in resp or []:
            for entry_id, fields in entries:
                env = Envelope.from_json(fields["data"])
                out.append((stream, entry_id, env))
        return out

    def ack(self, stream: str, group: str, entry_id: str) -> int:
        return self.r.xack(stream, group, entry_id)

    def claim_pending(
        self,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int = 0,
        count: int = 50,
    ) -> list:
        """Reclaim pending-but-unACKed messages with XAUTOCLAIM (the restart /
        recovery path for scenario 6). Returns ``[(entry_id, env), ...]``."""
        out = []
        cursor = "0-0"
        while True:
            cursor, claimed, _deleted = self.r.xautoclaim(
                stream, group, consumer, min_idle_ms, start_id=cursor, count=count
            )
            for entry_id, fields in claimed:
                if not fields:  # entry vanished (e.g. trimmed); skip
                    continue
                env = Envelope.from_json(fields["data"])
                out.append((entry_id, env))
            if cursor == "0-0":
                break
        return out

    def pending_count(self, stream: str, group: str) -> int:
        return self.r.xpending(stream, group)["pending"]

    # ---- offline hold queue (spec §13.6 decision: hold & deliver when online) -
    def hold(self, env: Envelope) -> int:
        """Park a Comet for an offline recipient on a per-target hold list."""
        return self.r.rpush(f"aether:hold:{env.to}", env.to_json())

    def hold_len(self, project_id: str) -> int:
        return self.r.llen(f"aether:hold:{project_id}")

    def drain_hold(self, project_id: str) -> list:
        """Pop every held envelope for a project (oldest first), FIFO."""
        key = f"aether:hold:{project_id}"
        out = []
        while True:
            raw = self.r.lpop(key)
            if raw is None:
                return out
            out.append(Envelope.from_json(raw))

    # ---- paused-inbound hold (operator pause, spec §18.2) ------------------
    def hold_inbound(self, project_id: str, env: Envelope) -> int:
        """Park an inbound message a paused Observatory must not process yet.

        Distinct from the offline ``hold`` (which parks OUTBOUND replies). On
        resume this is redelivered straight to the inbox WITHOUT re-mirroring to
        aether:events, so a paused-then-resumed message does not create a second
        'message' timeline event."""
        return self.r.rpush(f"aether:pausehold:{project_id}", env.to_json())

    def inbound_hold_len(self, project_id: str) -> int:
        return self.r.llen(f"aether:pausehold:{project_id}")

    def flush_inbound_hold(self, project_id: str) -> list:
        """Redeliver every paused inbound back to the body's inbox stream (no
        mirror). Returns the redelivered envelopes."""
        key = f"aether:pausehold:{project_id}"
        out = []
        while True:
            raw = self.r.lpop(key)
            if raw is None:
                return out
            env = Envelope.from_json(raw)
            self.r.xadd(inbox_stream(project_id), {"data": raw})  # inbox only, no mirror
            out.append(env)

    # ---- reading the mirror (Stargazer / test assertions) ------------------
    def read_events(self, start: str = "-", end: str = "+") -> list:
        """Return all aether:events records (parsed), oldest first."""
        out = []
        for _entry_id, fields in self.r.xrange(EVENTS_STREAM, min=start, max=end):
            out.append(json.loads(fields["data"]))
        return out
