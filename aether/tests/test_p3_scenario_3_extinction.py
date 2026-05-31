"""Phase 3 · Scenario 3 — 熄滅可見 (spec §16.1-3).

The five extinction reasons (horizon / rate / dedup / malformed_output /
recipient_offline) each appear in the Terminated Log with the correct canonical
reason. Proven against a hand-crafted stream AND against what the REAL system
actually emits (rate_limited canonicalizes to rate).
"""
from aether.core.envelope import new_envelope
from aether.observatory.claude_runner import ClaudeTurn
from aether.stargazer.events import EventReader
from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.viewmodels import EXTINCTION_REASONS, build_extinction_log
from .harness import always_reply, drain, never_reply, new_conversation_id, pump
from .p3_fixtures import seed, term


def _ext(r):
    records = [rec for _id, rec in EventReader(ReadOnlyRedis(r)).recent(2000)]
    return build_extinction_log(records)


def test_five_reasons_visible_from_fixture(r):
    cid = "c"
    seed(r, [
        term(cid, "horizon"), term(cid, "rate_limited"), term(cid, "dedup"),
        term(cid, "malformed_output"), term(cid, "recipient_offline"),
    ])
    reasons = {e.reason for e in _ext(r)}
    assert reasons == {"horizon", "rate", "dedup", "malformed_output", "recipient_offline"}
    assert len(_ext(r)) == 5


def test_ack_suppressed_reason_is_recognized(r):
    # §16.1-3 (updated by §17): the new register-gate reason must be surfaced too.
    seed(r, [term("c", "ack_suppressed", to="project_alpha")])
    log = _ext(r)
    assert len(log) == 1 and log[0].reason == "ack_suppressed"
    assert "ack_suppressed" in EXTINCTION_REASONS  # in the reason vocabulary


def test_ack_suppressed_from_real_register_gate(make_p2_obs, client, heartbeat, r):
    # The real §17.1 gate emits an ack_suppressed event the dashboard picks up.
    from aether.observatory.claude_runner import ClaudeTurn
    from aether.core.envelope import new_envelope
    from .harness import control_json
    heartbeat.beat("project_alpha")
    obs, _ = make_p2_obs("project_beta",
                         lambda inv: ClaudeTurn(raw_text=control_json(True, intent="ack", text="thanks!"),
                                                session_id="s"))
    client.emit(new_envelope(from_="project_alpha", to="project_beta", intent="inform", text="done"))
    drain(obs)
    assert any(e.reason == "ack_suppressed" for e in _ext(r))


def test_five_reasons_from_real_system_emissions(make_obs, make_p2_obs, client, heartbeat, r):
    # horizon — forced ping-pong, max_hops=2
    a = make_obs("project_alpha", always_reply(text="x"), consumer="a-h")
    b = make_obs("project_beta", always_reply(text="y"), consumer="b-h")
    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="ask", text="q", max_hops=2))
    pump([a, b])

    # rate — flood one conversation past a cap of 1
    br = make_obs("project_beta", never_reply(), max_per_window=1, consumer="b-rate")
    cid = new_conversation_id()
    for i in range(3):
        client.emit(new_envelope(from_="ops", to="project_beta", intent="inform",
                                 text=str(i), conversation_id=cid))
    drain(br)

    # dedup — same message_id twice
    bd = make_obs("project_beta", never_reply(), consumer="b-dup")
    dup = new_envelope(from_="ops", to="project_beta", intent="inform", text="dup")
    client.emit(dup); client.emit(dup)
    drain(bd)

    # malformed_output — model emits no valid control block
    def malformed(inv):
        return ClaudeTurn(raw_text="just prose, no json", session_id="s")
    bm, _ = make_p2_obs("project_gamma", malformed, consumer="g-mal")
    client.emit(new_envelope(from_="ops", to="project_gamma", intent="ask", text="q"))
    drain(bm)

    # recipient_offline — reply routed to an offline body is held
    heartbeat.go_offline("project_gamma")
    bo, _ = make_p2_obs("project_beta", always_reply(to="project_gamma", text="x"),
                        consumer="b-off")
    client.emit(new_envelope(from_="ops", to="project_beta", intent="ask",
                             text="route to gamma"))
    drain(bo)

    reasons = {e.reason for e in _ext(r)}
    assert {"horizon", "rate", "dedup", "malformed_output", "recipient_offline"} <= reasons
