"""Phase 3 · Scenario 5 — 即時望遠鏡 (spec §16.1-5).

A running turn's milestones appear in order (turn_start → tool_use* → turn_done),
and once turn_done arrives the turn has ended. Progress fixtures are injected
directly (spec §16.2 allows faking the progress channel).
"""
from aether.stargazer.events import EventReader
from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.viewmodels import build_telescope
from .p3_fixtures import prog, seed


def _records(r):
    return [rec for _id, rec in EventReader(ReadOnlyRedis(r)).recent(1000)]


def test_milestones_in_order_and_turn_ends(r):
    cid = "c"
    seed(r, [
        prog(cid, "beta", "turn_start"),
        prog(cid, "beta", "tool_use", name="Read"),
        prog(cid, "beta", "tool_use", name="Grep"),
        prog(cid, "beta", "turn_done", subtype="success"),
    ])
    t = build_telescope(_records(r), cid)
    assert [m["kind"] for m in t.milestones] == ["turn_start", "tool_use", "tool_use", "turn_done"]
    assert [m.get("name") for m in t.milestones if m["kind"] == "tool_use"] == ["Read", "Grep"]
    assert t.ended is True


def test_telescope_scopes_to_latest_turn(r):
    cid = "c"
    seed(r, [
        prog(cid, "b", "turn_start"), prog(cid, "b", "turn_done"),   # turn 1 (finished)
        prog(cid, "b", "turn_start"), prog(cid, "b", "tool_use", name="X"),  # turn 2 (running)
    ])
    t = build_telescope(_records(r), cid)
    assert [m["kind"] for m in t.milestones] == ["turn_start", "tool_use"]
    assert t.ended is False  # current turn hasn't finished
