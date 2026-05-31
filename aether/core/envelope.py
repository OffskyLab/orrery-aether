"""信封（Envelope）— the single data contract of the whole system (spec §3.1).

All routing, loop-prevention and session decisions ride on this structure.
Kept as a plain dataclass with explicit (de)serialization so the wire format
is one obvious place, and so ``core/`` has no hidden coupling to anything.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# Allowed enum values, kept here so validation has one source of truth.
TYPES = ("comet", "wave")
INTENTS = ("ask", "inform", "task", "result", "ack")
BROADCAST = "broadcast"

DEFAULT_MAX_HOPS = 8  # Horizon default, spec §3.1

# Fixed namespace for deriving reply ids (spec §13.1). A reply's message_id is
# uuid5(NAMESPACE, inbound_message_id), so even if a crash causes the same reply
# to be emitted twice, the recipient's idempotency keys it identically and dedups
# it — making "emit a reply" end-to-end idempotent.
AETHER_NAMESPACE = uuid.UUID("a37e1100-0000-4000-8000-00000000a37e")


def derive_reply_id(inbound_message_id: str) -> str:
    return str(uuid.uuid5(AETHER_NAMESPACE, inbound_message_id))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Body:
    """信封的 body：意圖 + 自足的內文 + 選填結構化 context（spec §3.1）。"""

    intent: str
    text: str
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"intent": self.intent, "text": self.text, "context": self.context}

    @staticmethod
    def from_dict(d: dict) -> "Body":
        return Body(
            intent=d["intent"],
            text=d["text"],
            context=d.get("context") or {},
        )


@dataclass
class Envelope:
    message_id: str
    conversation_id: str
    from_: str
    to: str
    type: str
    reply_to: Optional[str]
    hop_count: int
    max_hops: int
    created_at: str
    body: Body
    # §18.1 Wave: a broadcast that EXPLICITLY solicits replies. Default False, i.e.
    # a Wave is an announcement, not the start of a conversation — the primary
    # fan-out anti-explosion control. Only meaningful for type == "wave".
    solicit: bool = False

    # ---- (de)serialization -------------------------------------------------
    def to_dict(self) -> dict:
        # Wire field is "from" (Python reserves the bare word), hence from_.
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "from": self.from_,
            "to": self.to,
            "type": self.type,
            "reply_to": self.reply_to,
            "hop_count": self.hop_count,
            "max_hops": self.max_hops,
            "created_at": self.created_at,
            "solicit": self.solicit,
            "body": self.body.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict) -> "Envelope":
        env = Envelope(
            message_id=d["message_id"],
            conversation_id=d["conversation_id"],
            from_=d["from"],
            to=d["to"],
            type=d["type"],
            reply_to=d.get("reply_to"),
            hop_count=int(d["hop_count"]),
            max_hops=int(d["max_hops"]),
            created_at=d["created_at"],
            solicit=bool(d.get("solicit", False)),  # backward-compatible default
            body=Body.from_dict(d["body"]),
        )
        env.validate()
        return env

    @staticmethod
    def from_json(s: str) -> "Envelope":
        return Envelope.from_dict(json.loads(s))

    # ---- validation --------------------------------------------------------
    def validate(self) -> "Envelope":
        if self.type not in TYPES:
            raise ValueError(f"invalid envelope.type: {self.type!r}")
        if self.body.intent not in INTENTS:
            raise ValueError(f"invalid body.intent: {self.body.intent!r}")
        if not self.body.text or not str(self.body.text).strip():
            # spec §3.1: body.text 必須自足 — empty text is never self-contained.
            raise ValueError("body.text must be non-empty (envelopes must be self-contained)")
        if self.hop_count < 0:
            raise ValueError("hop_count must be >= 0")
        if self.max_hops < 1:
            raise ValueError("max_hops must be >= 1")
        if self.type == "wave" and self.to != BROADCAST:
            raise ValueError("wave envelopes must target 'broadcast'")
        if self.type == "comet" and self.to == BROADCAST:
            raise ValueError("comet envelopes must target a concrete project_id")
        return self


def new_envelope(
    *,
    from_: str,
    to: str,
    intent: str,
    text: str,
    conversation_id: Optional[str] = None,
    reply_to: Optional[str] = None,
    hop_count: int = 0,
    max_hops: int = DEFAULT_MAX_HOPS,
    context: Optional[dict] = None,
    type_: Optional[str] = None,
    message_id: Optional[str] = None,
    created_at: Optional[str] = None,
    solicit: bool = False,
) -> Envelope:
    """Mint a fresh, validated envelope.

    ``hop_count`` defaults to 0: a freshly-minted *origin* message has travelled
    zero hops. Each reply increments it by one (see observatory.main / spec §5.2).
    ``type`` is inferred from ``to`` unless explicitly given.
    """
    if type_ is None:
        type_ = "wave" if to == BROADCAST else "comet"
    env = Envelope(
        message_id=message_id or str(uuid.uuid4()),
        conversation_id=conversation_id or str(uuid.uuid4()),
        from_=from_,
        to=to,
        type=type_,
        reply_to=reply_to,
        hop_count=hop_count,
        max_hops=max_hops,
        created_at=created_at or _utcnow_iso(),
        solicit=bool(solicit),
        body=Body(intent=intent, text=text, context=context or {}),
    )
    return env.validate()


def make_reply(
    parent: Envelope,
    *,
    from_: str,
    to: Optional[str],
    intent: str,
    text: str,
    context: Optional[dict] = None,
    message_id: Optional[str] = None,
) -> Envelope:
    """Build a reply to ``parent``, carrying Horizon forward (hop_count + 1).

    Same ``conversation_id`` (邏輯對話串), ``reply_to`` points at the parent's
    message_id, and ``max_hops`` is inherited so the Horizon ceiling is stable
    across the whole conversation. ``message_id`` is normally the *derived* id
    (``derive_reply_id(parent.message_id)``) so the reply is idempotent (§13.1).

    A reply is ALWAYS a directed Comet — never a Wave. §18.1 forbids a Wave from
    being emitted as a reply (broadcast-storm prevention); this is the hard guard
    that makes it impossible to construct one.
    """
    resolved_to = to or parent.from_  # default: reply back to sender (spec §5.2)
    if resolved_to == BROADCAST:
        raise ValueError("a reply may never be a Wave (to='broadcast' is forbidden, §18.1)")
    return new_envelope(
        from_=from_,
        to=resolved_to,
        intent=intent,
        text=text,
        conversation_id=parent.conversation_id,
        reply_to=parent.message_id,
        hop_count=parent.hop_count + 1,  # Horizon 遞增
        max_hops=parent.max_hops,
        context=context,
        message_id=message_id,
    )
