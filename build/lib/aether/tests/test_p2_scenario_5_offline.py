"""Phase 2 · Scenario 5 — 離線目標 (spec §14.1-5, §13.6 decision: HOLD).

A Comet to a target with no heartbeat is HELD (queued) and logged with
reason=recipient_offline — deterministic and assertable. When the target comes
online it flushes its hold queue and the message is delivered & processed.
"""
from aether.core.envelope import new_envelope
from .harness import always_reply, drain, never_reply


def test_offline_target_is_held_then_delivered_on_return(make_p2_obs, client, heartbeat):
    heartbeat.go_offline("project_gamma")  # target offline
    obs_b, _ = make_p2_obs("project_beta", always_reply(to="project_gamma", text="for gamma"))

    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="ask", text="route to the offline body"))
    drain(obs_b)

    # ── held, not delivered ──
    assert client.hold_len("project_gamma") == 1
    assert client.r.xlen("aether:inbox:project_gamma") == 0
    events = client.read_events()
    held = [e for e in events if e["kind"] == "held"]
    assert len(held) == 1 and held[0]["reason"] == "recipient_offline" and held[0]["to"] == "project_gamma"

    # ── gamma returns online and flushes its hold queue → delivered & processed ──
    obs_g, _ = make_p2_obs("project_gamma", never_reply(), online=True)
    moved = obs_g.flush_hold()
    assert len(moved) == 1
    assert client.hold_len("project_gamma") == 0
    assert client.r.xlen("aether:inbox:project_gamma") == 1

    drain(obs_g)
    done = [e for e in client.read_events() if e["kind"] == "processing_done"
            and e["to"] == "project_gamma"]
    assert len(done) == 1
