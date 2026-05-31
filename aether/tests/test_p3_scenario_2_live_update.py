"""Phase 3 · Scenario 2 — 即時更新 (spec §16.1-2).

Append events to aether:events → a following reader receives them in order within
a small delay. Proven at the tail() level (deterministic) and through the SSE
generator (the actual endpoint path).
"""
import asyncio

from aether.stargazer.events import EventReader
from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.server import sse_source
from .p3_fixtures import done, msg, seed


def test_tail_receives_appended_events_in_order(r):
    cid = "c"
    seed(r, [msg(cid, "a", "b", 0)])
    reader = EventReader(ReadOnlyRedis(r))
    cursor = reader.latest_id()

    new_ids = seed(r, [msg(cid, "b", "a", 1), done(cid, "a", "done")])
    batch = reader.tail(cursor, block_ms=200)

    assert [eid for eid, _ in batch] == new_ids               # exact, in order
    assert [rec["event_type"] for _id, rec in batch] == ["message", "done"]


def test_sse_source_emits_backlog_with_ids(r):
    cid = "c"
    seed(r, [msg(cid, "a", "b", 0), msg(cid, "b", "a", 1)])
    reader = EventReader(ReadOnlyRedis(r))

    async def collect():
        out = []
        async for ev in sse_source(reader, None, recent_window=50,
                                   block_ms=10, max_idle_polls=1):
            out.append(ev)
        return out

    out = asyncio.run(collect())
    assert [e["event"] for e in out] == ["message", "message"]
    assert all(e.get("id") for e in out)  # ids present → reconnect cursor works
