"""Phase 3 · Scenario 7 — 規模與重連 (spec §16.1-7).

Initial load reads only a bounded recent window (not the whole history); a
reconnect that carries the last-seen id resumes strictly after it, so nothing
already displayed is re-sent.
"""
import asyncio

from aether.stargazer.events import EventReader
from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.server import sse_source
from .p3_fixtures import msg, seed


def test_initial_load_is_bounded(r):
    cid = "c"
    seed(r, [msg(cid, "a", "b", i % 8) for i in range(500)])
    reader = EventReader(ReadOnlyRedis(r))

    recent = reader.recent(window=100)
    assert len(recent) == 100                      # bounded, not 500
    assert recent[-1][0] == reader.latest_id()     # newest 100, chronological tail


def test_reconnect_after_cursor_has_no_overlap(r):
    cid = "c"
    seed(r, [msg(cid, "a", "b", 0), msg(cid, "b", "a", 1)])
    reader = EventReader(ReadOnlyRedis(r))
    first = reader.recent(100)
    last_id = first[-1][0]

    seed(r, [msg(cid, "a", "b", 2)])               # arrives during disconnect
    resumed = reader.after(last_id)

    first_ids = {eid for eid, _ in first}
    resumed_ids = {eid for eid, _ in resumed}
    assert first_ids.isdisjoint(resumed_ids)       # nothing re-sent
    assert len(resumed) == 1


def test_fresh_connect_cursor_closes_the_empty_stream_gap(r):
    """Regression for the fresh-connect-on-empty-stream gap (§16.1-7 "no gap").

    A fresh connect with an empty backlog must tail from "0", not "$": "$" is
    resolved to the live tip when XREAD runs, so an event that landed in the gap
    would be skipped. With "0" it is delivered. This contrasts both directly."""
    # Simulate an event that arrived in the gap (after recent() returned empty).
    ids = seed(r, [msg("c", "a", "b", 0)])
    reader = EventReader(ReadOnlyRedis(r))

    # "$" (the old behaviour) resolves to the current tip → would DROP the event.
    assert reader.tail("$", block_ms=30) == []
    # "0" (the fix used for an empty backlog) → the event is delivered, no gap.
    got = reader.tail("0", block_ms=30)
    assert [eid for eid, _ in got] == ids


def test_sse_reconnect_with_last_event_id_sends_no_backlog(r):
    cid = "c"
    seed(r, [msg(cid, "a", "b", 0), msg(cid, "b", "a", 1)])
    reader = EventReader(ReadOnlyRedis(r))
    last_id = reader.latest_id()
    seed(r, [msg(cid, "a", "b", 2)])               # one new event after reconnect point

    async def collect():
        out = []
        async for ev in sse_source(reader, last_id, block_ms=10, max_idle_polls=1):
            out.append(ev)
        return out

    out = asyncio.run(collect())
    assert len(out) == 1                            # only the new event, no backlog
