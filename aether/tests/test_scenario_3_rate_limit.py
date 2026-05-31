"""Scenario 3 · 速率限制 (spec §11.1.3, layer 3).

Flood the same conversation_id past the per-window cap inside one window. Assert
the overflow is blocked and logged with reason=rate_limited. Then advance the
INJECTABLE clock past the window and show the counter resets — proving the
window is clock-driven and testable in milliseconds (spec §11.2), with no real
one-minute wait.
"""
from aether.core.envelope import new_envelope
from .harness import drain, never_reply, new_conversation_id


def test_rate_limit_blocks_overflow_and_resets_next_window(client, make_obs, clock):
    cap = 5
    obs_b = make_obs("project_beta", never_reply(), max_per_window=cap, window_seconds=60)

    cid = new_conversation_id()

    def send(text):
        client.emit(new_envelope(
            from_="project_alpha", to="project_beta", intent="inform",
            text=text, conversation_id=cid,
        ))

    # Window 1: send cap + 3 within the same (frozen) clock window.
    for i in range(cap + 3):
        send(f"burst {i}")
    drain(obs_b)

    events = client.read_events()
    processed = [e for e in events if e["kind"] == "processing_done"]
    limited = [e for e in events if e["kind"] == "terminated" and e["reason"] == "rate_limited"]
    assert len(processed) == cap          # exactly the first `cap` got through
    assert len(limited) == 3              # the 3 overflow messages were blocked
    assert obs_b.runner.call_count == cap  # blocked ones never reached Claude

    # ── advance the injectable clock into the NEXT window → counter resets ──
    clock.advance(60)
    send("after window reset")
    drain(obs_b)

    events = client.read_events()
    processed = [e for e in events if e["kind"] == "processing_done"]
    limited = [e for e in events if e["kind"] == "terminated" and e["reason"] == "rate_limited"]
    assert len(processed) == cap + 1      # the post-reset message went through
    assert len(limited) == 3              # still just the original 3
