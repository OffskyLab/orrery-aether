"""View models (spec §15.4) — pure functions: events → structured render state.

These are the unit-tested core of Stargazer (spec §16.2 tests the view model, not
pixels). The headline invariant (§16.1-1): the timeline must equal the event
stream, hop-for-hop, with no ghost comets and nothing dropped.

All functions take a list of parsed event records (the §15.1 contract) and are
total/pure — no Redis, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# The extinction reasons Stargazer's 熄滅紀錄 surfaces (spec §16.1-3, updated by
# §17 to include ack_suppressed). The system emits "rate_limited"; we canonicalize
# it to "rate" (§15.1 vocab). build_extinction_log surfaces ALL terminated events
# regardless of reason, so this list is the documented vocabulary, not a filter —
# any reason filter that IS hardcoded must include ack_suppressed.
_CANON_REASON = {"rate_limited": "rate"}
EXTINCTION_REASONS = ("horizon", "rate", "dedup", "malformed_output",
                      "recipient_offline", "ack_suppressed", "operator_kill")


def canon_reason(reason: Optional[str]) -> Optional[str]:
    return _CANON_REASON.get(reason, reason)


# ── Timeline (§15.4-2, §16.1-1) ─────────────────────────────────────────────
@dataclass
class Hop:
    seq: int
    from_: str
    to: str
    hop_count: int
    intent: str
    text: str
    message_id: str
    conversation_id: str


@dataclass
class Timeline:
    conversation_id: Optional[str]
    hops: List[Hop] = field(default_factory=list)
    summaries: List[dict] = field(default_factory=list)
    terminal: Optional[dict] = None  # the extinction event that ended it, if any
    actions: List[dict] = field(default_factory=list)  # §18.2 operator actions, in order

    @property
    def hop_tuples(self):
        """(from, to, hop_count) per hop — the fidelity assertion target."""
        return [(h.from_, h.to, h.hop_count) for h in self.hops]


def build_timeline(records: List[dict], conversation_id: Optional[str] = None) -> Timeline:
    """Reconstruct a conversation's hops EXACTLY from the event stream.

    One Hop per ``event_type=message`` event, in stream order — no more, no less.
    ``done`` summaries and the terminating extinction event are attached for the
    Timeline view, but never invent or drop a hop."""
    tl = Timeline(conversation_id=conversation_id)
    seq = 0
    for rec in records:
        if conversation_id is not None and rec.get("conversation_id") != conversation_id:
            continue
        et = rec.get("event_type")
        if et == "message":
            env = rec.get("envelope") or {}
            frm, to, hc = env.get("from"), env.get("to"), env.get("hop_count")
            # Never invent a ghost comet (§16.1-1): a renderable hop needs real
            # routing. A message record missing from/to/hop_count is not a hop —
            # the real system never emits one, so skipping it can't drop a true
            # hop, but it does refuse to fabricate one from a malformed record.
            if frm is None or to is None or hc is None:
                continue
            body = env.get("body") or {}
            tl.hops.append(Hop(
                seq=seq, from_=frm, to=to,
                hop_count=int(hc),  # coerce: hop_tuples compare against ints
                intent=body.get("intent", ""),
                text=body.get("text", ""), message_id=env.get("message_id"),
                conversation_id=rec.get("conversation_id"),
            ))
            seq += 1
        elif et == "done":
            tl.summaries.append({
                "by": rec.get("to"), "summary": rec.get("summary", ""),
                "message_id": rec.get("message_id"),
            })
        elif et == "terminated":
            # The last extinction event for this conversation is how it ended.
            tl.terminal = {
                "reason": canon_reason(rec.get("reason")),
                "to": rec.get("to"), "from": rec.get("from"),
                "hop_count": rec.get("hop_count"), "message_id": rec.get("message_id"),
            }
        elif et == "operator_action":
            # §18.2 / §19.1-7: operator actions are visible on the timeline.
            tl.actions.append({
                "actor": rec.get("actor"), "action": rec.get("action"),
                "ts": rec.get("ts"), "reason": rec.get("reason"),
            })
    return tl


# ── Constellation / star map (§15.4-1, §16.1-4) ─────────────────────────────
@dataclass
class Star:
    project_id: str
    activity: int  # event count touching this Body → brightness
    online: bool


def build_constellation(records: List[dict], online: Dict[str, bool],
                        bodies: Optional[List[str]] = None) -> Dict[str, Star]:
    """Stars with brightness = activity and online flag from heartbeat.

    ``online`` is the authoritative liveness map (read from heartbeat keys). A
    Body with no live heartbeat is dim/offline (§16.1-4)."""
    activity: Dict[str, int] = {}
    known = set(bodies or [])
    for rec in records:
        env = rec.get("envelope") or {}
        involved = set()
        for who in (rec.get("from"), rec.get("to"), rec.get("project_id"),
                    env.get("from"), env.get("to")):
            if who and who != "broadcast":
                involved.add(who)
        for who in involved:  # one increment per body per event (brightness)
            activity[who] = activity.get(who, 0) + 1
            known.add(who)
    known.update(online.keys())
    return {pid: Star(project_id=pid, activity=activity.get(pid, 0),
                      online=bool(online.get(pid, False)))
            for pid in sorted(known)}


# ── Live telescope (§15.4-3, §16.1-5) ───────────────────────────────────────
@dataclass
class Telescope:
    conversation_id: str
    milestones: List[dict] = field(default_factory=list)  # [{kind, ...}] in order
    ended: bool = False  # turn_done seen → the running turn finished


def build_telescope(records: List[dict], conversation_id: str) -> Telescope:
    """Ordered progress milestones for the LATEST turn of a conversation.

    Milestones run turn_start → tool_use* → turn_done; once turn_done appears the
    turn has ended (the live view stops advancing for it)."""
    progress = [rec for rec in records
                if rec.get("event_type") == "progress"
                and rec.get("conversation_id") == conversation_id]
    # Scope to the latest turn (from the last turn_start onward).
    start_idx = 0
    for i, rec in enumerate(progress):
        if (rec.get("progress") or {}).get("kind") == "turn_start":
            start_idx = i
    turn = progress[start_idx:]
    milestones = [rec.get("progress") or {} for rec in turn]
    ended = any(m.get("kind") == "turn_done" for m in milestones)
    return Telescope(conversation_id=conversation_id, milestones=milestones, ended=ended)


# ── Terminated / extinction log (§15.4-4, §16.1-3) ──────────────────────────
@dataclass
class Extinction:
    reason: str
    conversation_id: Optional[str]
    from_: Optional[str]
    to: Optional[str]
    hop_count: Optional[int]
    message_id: Optional[str]
    ts: Optional[str]


def build_extinction_log(records: List[dict]) -> List[Extinction]:
    """Every event where a message was stopped/deferred, with its reason
    canonicalized — horizon / rate / dedup / malformed_output / recipient_offline
    / ack_suppressed / operator_kill, the data for tuning + debugging (§15.4-4)."""
    out: List[Extinction] = []
    for rec in records:
        if rec.get("event_type") != "terminated":
            continue
        reason = canon_reason(rec.get("reason"))
        out.append(Extinction(
            reason=reason, conversation_id=rec.get("conversation_id"),
            from_=rec.get("from"), to=rec.get("to"),
            hop_count=rec.get("hop_count"), message_id=rec.get("message_id"),
            ts=rec.get("ts"),
        ))
    return out


# ── Operator audit log (§18.2 / §19.1-8) ────────────────────────────────────
@dataclass
class OperatorAction:
    actor: Optional[str]
    action: Optional[str]
    conversation_id: Optional[str]
    ts: Optional[str]
    reason: Optional[str]
    detail: dict


def build_operator_log(records: List[dict]) -> List[OperatorAction]:
    """Every operator action, reconstructable from aether:events with actor +
    timestamp (the §19.1-8 audit-completeness view)."""
    known = {"event_type", "kind", "actor", "action", "conversation_id", "ts", "reason"}
    out: List[OperatorAction] = []
    for rec in records:
        if rec.get("event_type") != "operator_action":
            continue
        out.append(OperatorAction(
            actor=rec.get("actor"), action=rec.get("action"),
            conversation_id=rec.get("conversation_id"), ts=rec.get("ts"),
            reason=rec.get("reason"),
            detail={k: v for k, v in rec.items() if k not in known},
        ))
    return out
