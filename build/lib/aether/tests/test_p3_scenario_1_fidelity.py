"""Phase 3 · Scenario 1 — 重建忠實性 (spec §16.1-1, the headline invariant).

The rendered timeline must equal the event stream hop-for-hop: same hop counts,
from→to, order, and number of entries — no ghost comets, nothing dropped. Proven
two ways: against a hand-crafted known stream, and against a REAL pipeline run.
"""
from aether.core.envelope import new_envelope
from aether.stargazer.events import EventReader
from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.viewmodels import build_timeline
from .harness import reply_once_then_stop, never_reply, pump
from .p3_fixtures import done, msg, seed, term


def _records(r):
    return [rec for _id, rec in EventReader(ReadOnlyRedis(r)).recent(1000)]


def test_timeline_matches_handcrafted_stream(r):
    cid = "c1"
    seed(r, [
        msg(cid, "alpha", "beta", 0, intent="ask", text="what is the id field?"),
        done(cid, "beta", "beta answered: order_id"),
        msg(cid, "beta", "alpha", 1, intent="result", text="order_id (uuid)"),
        done(cid, "alpha", "alpha resolved"),
    ])
    tl = build_timeline(_records(r), conversation_id=cid)
    assert tl.hop_tuples == [("alpha", "beta", 0), ("beta", "alpha", 1)]
    assert len(tl.hops) == 2          # no duplicate, no missing
    assert len(tl.summaries) == 2
    assert tl.terminal is None        # natural convergence


def test_no_ghost_hop_from_malformed_message_record(r):
    """A message record missing routing fields must NOT become a (None,None,None)
    ghost comet — and a string hop_count must compare as an int (§16.1-1)."""
    cid = "c3"
    seed(r, [
        msg(cid, "a", "b", 0),
        {"event_type": "message", "kind": "message", "conversation_id": cid,
         "ts": "t", "envelope": {"conversation_id": cid, "body": {}}},  # malformed: no from/to/hop
        {"event_type": "message", "kind": "message", "conversation_id": cid, "ts": "t",
         "envelope": {"from": "b", "to": "a", "hop_count": "1",  # hop_count as STRING
                      "body": {"intent": "result", "text": "x"}, "message_id": "m"}},
    ])
    tl = build_timeline(_records(r), conversation_id=cid)
    assert tl.hop_tuples == [("a", "b", 0), ("b", "a", 1)]   # ghost skipped, "1"→1
    assert all(isinstance(h.hop_count, int) for h in tl.hops)


def test_timeline_marks_terminal_extinction(r):
    cid = "c2"
    seed(r, [
        msg(cid, "a", "b", 0), msg(cid, "b", "a", 1), msg(cid, "a", "b", 2),
        term(cid, "horizon", frm="b", to="a", hop=3),
    ])
    tl = build_timeline(_records(r), conversation_id=cid)
    assert tl.hop_tuples == [("a", "b", 0), ("b", "a", 1), ("a", "b", 2)]
    assert tl.terminal["reason"] == "horizon"


def test_timeline_reconstructs_a_real_pipeline_run(make_obs, client, r):
    """End-to-end: drive a real (fake-claude) convergence through the Observatory,
    then rebuild it purely from aether:events — render == event stream."""
    obs_a = make_obs("project_alpha", never_reply())
    obs_b = make_obs("project_beta", reply_once_then_stop(text="order_id (uuid)"))
    ask = new_envelope(from_="project_alpha", to="project_beta", intent="ask", text="q")
    client.emit(ask)
    pump([obs_a, obs_b])

    tl = build_timeline(_records(r), conversation_id=ask.conversation_id)
    assert tl.hop_tuples == [
        ("project_alpha", "project_beta", 0),
        ("project_beta", "project_alpha", 1),
    ]
    assert len(tl.hops) == 2
