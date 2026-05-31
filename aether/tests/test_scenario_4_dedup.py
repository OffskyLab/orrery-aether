"""Scenario 4 · 去重 (spec §11.1.4).

The same message_id is delivered twice. Assert it is processed only once,
verified by the side-effect count (Claude invocations + processing_done events).
"""
from aether.core.envelope import new_envelope
from .harness import drain, never_reply


def test_duplicate_message_id_processed_once(client, make_obs):
    obs_b = make_obs("project_beta", never_reply())

    env = new_envelope(
        from_="project_alpha", to="project_beta", intent="inform",
        text="B should record this exactly once.",
    )
    # Deliver the SAME envelope (same message_id) twice into B's inbox.
    client.emit(env)
    client.emit(env)

    drain(obs_b)

    # ── side effect happened exactly once ──
    assert obs_b.runner.call_count == 1
    events = client.read_events()
    assert len([e for e in events if e["kind"] == "processing_done"]) == 1

    # ── the duplicate was recognised and skipped, not silently dropped ──
    assert len([e for e in events if e["kind"] == "duplicate_skipped"]) == 1
