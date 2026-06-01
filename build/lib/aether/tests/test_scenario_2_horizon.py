"""Scenario 2 · Horizon 強制觸發 (spec §11.1.2) — the most critical test.

Both sides ALWAYS reply (test mode), so nothing but the Horizon can stop the
ping-pong. Assert it stops at a fixed, predictable hop count, the last event is
reason=horizon, and no further messages are produced. Crucially, vary max_hops
and assert the stopping point scales proportionally — this proves the guard is
doing the stopping (and catches off-by-one), not a conversation that happened
to end on its own.
"""
import pytest

from aether.core.aether_client import BROADCAST_STREAM, inbox_stream
from aether.core.envelope import new_envelope
from .harness import always_reply, pump


def _run_pingpong(client, make_obs, max_hops):
    obs_a = make_obs("project_alpha", always_reply(text="A insists on continuing"))
    obs_b = make_obs("project_beta", always_reply(text="B insists on continuing"))

    ask = new_envelope(
        from_="project_alpha", to="project_beta", intent="ask",
        text="A pokes B; both are scripted to never stop replying.",
        max_hops=max_hops,
    )
    client.emit(ask)
    pump([obs_a, obs_b])
    return obs_a, obs_b


@pytest.mark.parametrize("max_hops", [2, 4, 8])
def test_horizon_stops_at_exactly_max_hops(client, make_obs, r, max_hops):
    obs_a, obs_b = _run_pingpong(client, make_obs, max_hops)
    events = client.read_events()

    # ── exactly one horizon termination, at hop_count == max_hops ──
    terminated = [e for e in events if e["kind"] == "terminated"]
    assert len(terminated) == 1, terminated
    assert terminated[0]["reason"] == "horizon"
    assert terminated[0]["hop_count"] == max_hops  # off-by-one pinned here

    # ── the last thing that happened was the horizon kill ──
    assert events[-1]["kind"] == "terminated" and events[-1]["reason"] == "horizon"

    # ── proportional: Claude was invoked exactly max_hops times (hops 0..M-1) ──
    invocations = obs_a.runner.call_count + obs_b.runner.call_count
    assert invocations == max_hops

    # ── no message ever exceeded the ceiling; the hop==M one was the terminated reply ──
    msg_hops = [e["envelope"]["hop_count"] for e in events if e["kind"] == "message"]
    assert max(msg_hops) == max_hops

    # ── silence afterwards: nothing left pending, nothing new redelivered ──
    for stream in (inbox_stream("project_alpha"), inbox_stream("project_beta")):
        assert client.pending_count(stream, f"grp-{stream.split(':')[-1]}") == 0
    assert pump([obs_a, obs_b]) == 1  # one empty round → already quiescent


def test_horizon_scales_strictly_with_max_hops(client, make_obs, r):
    """Same harness, three ceilings, asserting strict proportionality in one
    place so a constant/hard-coded stop can't sneak past the per-case tests."""
    seen = {}
    for max_hops in (2, 4, 8):
        r.flushdb()
        obs_a, obs_b = _run_pingpong(client, make_obs, max_hops)
        seen[max_hops] = obs_a.runner.call_count + obs_b.runner.call_count
    assert seen == {2: 2, 4: 4, 8: 8}, seen
