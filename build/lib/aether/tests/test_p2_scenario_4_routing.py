"""Phase 2 · Scenario 4 — 路由：Claude 選收件者 (spec §14.1-4, §13.4).

Given an injected registry, the recipient Claude picks ("to") is honoured and the
Comet lands in the right inbox; an unknown/invalid "to" is rejected and logged
(fail-safe), never sent.
"""
from aether.core.envelope import new_envelope
from .harness import always_reply, drain


def test_valid_claude_selected_recipient_is_routed(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_gamma")  # chosen target online
    obs, _ = make_p2_obs("project_beta", always_reply(to="project_gamma", text="for gamma"))

    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="ask", text="please route onward"))
    drain(obs)

    # landed in gamma's inbox, NOT bounced back to the original sender
    assert client.r.xlen("aether:inbox:project_gamma") == 1
    assert client.r.xlen("aether:inbox:project_alpha") == 0
    msg = [e for e in client.read_events() if e["kind"] == "message"
           and e["envelope"]["from"] == "project_beta"][0]
    assert msg["envelope"]["to"] == "project_gamma"


def test_invalid_recipient_is_rejected_and_logged(make_p2_obs, client):
    obs, _ = make_p2_obs("project_beta", always_reply(to="project_nonexistent", text="bad"))

    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="ask", text="try to route to a ghost"))
    drain(obs)

    events = client.read_events()
    rejected = [e for e in events if e["kind"] == "reply_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "invalid_recipient"
    assert rejected[0]["to"] == "project_nonexistent"
    # nothing was sent anywhere
    assert client.r.exists("aether:inbox:project_nonexistent") == 0
    assert [e for e in events if e["kind"] == "message"
            and e["envelope"]["from"] == "project_beta"] == []
