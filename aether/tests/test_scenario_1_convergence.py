"""Scenario 1 · 正常收斂 (spec §11.1.1).

A sends an ask to B → B replies → A decides it's resolved (reply_needed:false)
→ the conversation ends on its own. Assert total hops < max_hops and that
aether:events records every hop's from→to→hop_count.
"""
from aether.core.envelope import new_envelope
from .harness import never_reply, pump, reply_once_then_stop


def test_normal_convergence(client, make_obs):
    obs_a = make_obs("project_alpha", never_reply(summary="A: solved, done"))
    obs_b = make_obs("project_beta", reply_once_then_stop(text="B: the field is `id` (uuid)."))

    ask = new_envelope(
        from_="project_alpha", to="project_beta", intent="ask",
        text="A needs to know B's primary-key field name for the orders table.",
    )
    client.emit(ask)

    rounds = pump([obs_a, obs_b])
    assert rounds >= 1

    # ── conversation shape: exactly ask (A→B, hop0) then reply (B→A, hop1) ──
    messages = [e for e in client.read_events() if e["kind"] == "message"]
    hops = [(m["envelope"]["from"], m["envelope"]["to"], m["envelope"]["hop_count"])
            for m in messages]
    assert hops == [
        ("project_alpha", "project_beta", 0),
        ("project_beta", "project_alpha", 1),
    ], hops

    # ── converged well within Horizon ──
    max_hop = max(m["envelope"]["hop_count"] for m in messages)
    assert max_hop < ask.max_hops  # 1 < 8

    # ── each side ran Claude exactly once; A chose not to reply (layer 2) ──
    assert obs_b.runner.call_count == 1
    assert obs_a.runner.call_count == 1

    # ── no termination markers: this ended naturally, not by a guard ──
    assert [e for e in client.read_events() if e["kind"] == "terminated"] == []

    # ── every hop is reconstructable from aether:events (Stargazer timeline) ──
    done = [e for e in client.read_events() if e["kind"] == "processing_done"]
    assert len(done) == 2
