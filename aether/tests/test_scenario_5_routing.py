"""Scenario 5 · 路由正確 (spec §11.1.5).

A Comet addressed to B must be processed by B's Observatory only — A's
Observatory must never see it. The broker (inbox-per-project) does the routing;
clients don't receive-all-and-filter.
"""
from aether.core.envelope import new_envelope
from .harness import never_reply, pump


def test_comet_to_b_not_seen_by_a(client, make_obs, r):
    obs_a = make_obs("project_alpha", never_reply())
    obs_b = make_obs("project_beta", never_reply())

    comet = new_envelope(
        from_="project_alpha", to="project_beta", intent="task",
        text="A directs this task specifically at B.",
    )
    client.emit(comet)

    pump([obs_a, obs_b])

    # ── only B handled it ──
    assert obs_b.runner.call_count == 1
    assert obs_a.runner.call_count == 0

    # ── it physically landed only in B's inbox stream (A's stream exists but is
    #    empty — ensure_group(mkstream=True) creates the key, routing keeps it empty) ──
    assert r.xlen("aether:inbox:project_beta") == 1
    assert r.xlen("aether:inbox:project_alpha") == 0

    # ── the single processing_done belongs to B ──
    done = [e for e in client.read_events() if e["kind"] == "processing_done"]
    assert len(done) == 1
    assert done[0]["envelope"]["to"] == "project_beta"
