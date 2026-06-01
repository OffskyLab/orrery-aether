"""Hand-crafted aether:events fixtures for Phase 3 tests (spec §16.2).

These build records in the §15.1 contract and XADD them directly, so a test has
a known, exact event stream to assert the view models against — no real claude.
"""
from __future__ import annotations

import json
from typing import List

from aether.core.aether_client import EVENTS_STREAM

_TS = "2026-05-30T00:00:00+00:00"


def seed(r, records: List[dict]) -> List[str]:
    return [r.xadd(EVENTS_STREAM, {"data": json.dumps(rec, ensure_ascii=False)})
            for rec in records]


def msg(cid, frm, to, hop, *, intent="ask", text="t", mid=None) -> dict:
    mid = mid or f"{cid}-{frm}-{to}-{hop}"
    return {
        "event_type": "message", "kind": "message", "ts": _TS, "conversation_id": cid,
        "envelope": {
            "message_id": mid, "conversation_id": cid, "from": frm, "to": to,
            "type": "comet", "reply_to": None, "hop_count": hop, "max_hops": 8,
            "created_at": _TS, "body": {"intent": intent, "text": text, "context": {}},
        },
    }


def done(cid, to, summary, *, mid="m") -> dict:
    return {"event_type": "done", "kind": "processing_done", "ts": _TS,
            "conversation_id": cid, "to": to, "from": None, "summary": summary,
            "message_id": mid}


def term(cid, reason, *, frm="a", to="b", hop=8, mid="m") -> dict:
    return {"event_type": "terminated", "kind": "terminated", "ts": _TS,
            "conversation_id": cid, "reason": reason, "from": frm, "to": to,
            "hop_count": hop, "message_id": mid}


def prog(cid, pid, kind, **detail) -> dict:
    return {"event_type": "progress", "kind": "progress", "ts": _TS,
            "conversation_id": cid, "project_id": pid,
            "progress": {"kind": kind, **detail}}
