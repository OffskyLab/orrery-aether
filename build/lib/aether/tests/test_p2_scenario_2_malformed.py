"""Phase 2 · Scenario 2 — 畸形輸出 fail-safe (spec §14.1-2, §13.2).

Inject illegal control output. Assert: no reply, reason=malformed_output logged,
no crash/loop, and the retry is strictly bounded (exactly one). A second test
shows the bounded retry actually *recovers* when the re-output is valid.
"""
from aether.core.envelope import new_envelope
from aether.observatory.claude_runner import ClaudeTurn
from .harness import control_json, drain


def _malformed(text="Sure, here's my thinking but no machine-readable block at all."):
    def _r(inv):
        return ClaudeTurn(raw_text=text, session_id="sess-beta-1")
    return _r


def test_persistently_malformed_triggers_failsafe(make_p2_obs, client):
    obs, _crash = make_p2_obs("project_beta", _malformed(), malformed_retries=1)
    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="ask", text="please answer"))

    drain(obs)  # returns normally → no crash / no infinite loop

    # exactly one bounded retry: original + 1 = 2 calls, never more
    assert obs.runner.call_count == 2

    events = client.read_events()
    # fail-safe = no reply was emitted from beta
    assert [e for e in events if e["kind"] == "message"
            and e["envelope"]["from"] == "project_beta"] == []
    # the reason is recorded for Stargazer / debugging
    assert any(e.get("reason") == "malformed_output" for e in events)
    # not a termination by a guard — it just fell silent
    assert [e for e in events if e["kind"] == "terminated"] == []


def test_bounded_retry_recovers_on_valid_reoutput(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")  # reply target online so a recovered reply lands

    state = {"n": 0}

    def malformed_then_valid(inv):
        state["n"] += 1
        if state["n"] == 1:
            return ClaudeTurn(raw_text="oops, prose only", session_id="sess-beta-2")
        return ClaudeTurn(raw_text=control_json(True, text="recovered answer"),
                          session_id="sess-beta-2")

    obs, _ = make_p2_obs("project_beta", malformed_then_valid, malformed_retries=1)
    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="ask", text="answer me"))
    drain(obs)

    assert obs.runner.call_count == 2  # original + 1 successful retry
    events = client.read_events()
    # retry succeeded → a real reply went out, and NO malformed_output was logged
    assert client.r.xlen("aether:inbox:project_alpha") == 1
    assert not any(e.get("reason") == "malformed_output" for e in events)
