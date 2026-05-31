"""Phase 2 · Scenario 8 — 通訊語域：反客套與反附和 (spec §17, §17.5).

Deterministic coverage of the §17.1 HARD gates (ack-intent never leaves; the
conservative empty-content lint) and the §17.2/§17.3 register prompt mechanism.
The §17.5-4 behavioural critical-stance check is e2e/controlled (a separate gated
test); per §17.5 the hard rules are NOT relaxed because that one is hard to unit
test.
"""
import pytest

from aether.core.envelope import new_envelope
from aether.observatory.claude_runner import ClaudeTurn
from aether.observatory.prompt import compose_prompt
from aether.observatory.register import is_pure_social, register_gate
from .harness import control_json, drain, pump


def _turn(reply_needed, **kw):
    return lambda inv: ClaudeTurn(raw_text=control_json(reply_needed, **kw), session_id="s")


def _beta_msgs(client):
    return [e for e in client.read_events()
            if e["event_type"] == "message" and e["envelope"]["from"] == "project_beta"]


# ── §17.5-1: ack intent is suppressed ───────────────────────────────────────
def test_ack_intent_reply_is_suppressed(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")  # a NON-suppressed reply WOULD be deliverable
    obs, _ = make_p2_obs("project_beta", _turn(True, intent="ack", text="Thanks, got it!"))
    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="inform", text="The build is green."))
    drain(obs)

    assert _beta_msgs(client) == []                       # nothing left the station
    assert client.r.xlen("aether:inbox:project_alpha") == 0
    sup = [e for e in client.read_events() if e.get("reason") == "ack_suppressed"]
    assert len(sup) == 1
    assert sup[0]["event_type"] == "terminated" and sup[0]["gate"] == "intent_ack"


# ── §17.5-2: pleasantries do NOT extend the conversation (the headline proof) ─
def test_pleasantries_do_not_extend_conversation(make_p2_obs, client, heartbeat, r):
    """Run the SAME substantive exchange twice — once where A concludes silently
    (PURE), once where A instead *wants to thank* (POLITE, intent=ack). Assert the
    hop count is IDENTICAL: the pleasantry attempt added zero hops because the
    gate dropped it (spec §17.5-2). The POLITE run must show a real ack_suppressed
    — i.e. A genuinely tried to be polite and was stopped, it wasn't luck."""

    def run(a_is_polite, tag):
        # Distinct conversation per sub-run (no flushdb — that would wipe the
        # registry the fixture synced, getting B's reply rejected); filter by cid.
        heartbeat.beat("project_alpha"); heartbeat.beat("project_beta")
        cid = f"conv-{tag}"
        obs_b, _ = make_p2_obs("project_beta",
                               _turn(True, intent="result", text="The id field is order_id."),
                               consumer=f"b-{tag}")
        a_resp = (_turn(True, intent="ack", text="Perfect, thank you so much!")
                  if a_is_polite else _turn(False))  # polite → ack ; pure → conclude silently
        obs_a, _ = make_p2_obs("project_alpha", a_resp, consumer=f"a-{tag}")
        client.emit(new_envelope(from_="project_alpha", to="project_beta",
                                 intent="ask", text="What is the orders id field?",
                                 conversation_id=cid))
        pump([obs_a, obs_b])
        evs = [e for e in client.read_events() if e.get("conversation_id") == cid]
        hops = [e["envelope"]["hop_count"] for e in evs if e["event_type"] == "message"]
        suppressed = [e for e in evs if e.get("reason") == "ack_suppressed"]
        intents = [e["envelope"]["body"]["intent"] for e in evs if e["event_type"] == "message"]
        return max(hops), suppressed, intents

    pure_hops, pure_sup, _ = run(False, "pure")
    polite_hops, polite_sup, polite_intents = run(True, "polite")

    assert pure_hops == 1                       # substantive exchange = ask(0) + answer(1)
    assert polite_hops == pure_hops             # the thank-you added NO hop
    assert len(pure_sup) == 0                   # nothing suppressed when no pleasantry
    assert len(polite_sup) == 1                 # A's "thank you" WAS attempted and gated
    assert "ack" not in polite_intents          # no pleasantry ever rode the bus


# ── §17.5-2 (both sides): every pleasantry on either side is gated ──────────
def test_both_sides_pleasantries_are_all_suppressed(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha"); heartbeat.beat("project_beta")
    # Both sides are scripted to want to be polite (intent=ack) on any turn.
    obs_a, _ = make_p2_obs("project_alpha", _turn(True, intent="ack", text="Thanks!"), consumer="a")
    obs_b, _ = make_p2_obs("project_beta", _turn(True, intent="ack", text="Appreciate it!"), consumer="b")
    # One substantive FYI to each side — each would "thank back".
    client.emit(new_envelope(from_="project_beta", to="project_alpha", intent="inform", text="Deploy A is green."))
    client.emit(new_envelope(from_="project_alpha", to="project_beta", intent="inform", text="Deploy B is green."))
    pump([obs_a, obs_b])

    msgs = [e for e in client.read_events() if e["event_type"] == "message"]
    # Only the two FYIs ever rode the bus — neither thank-you did.
    assert len(msgs) == 2
    assert all(m["envelope"]["body"]["intent"] != "ack" for m in msgs)
    # Both sides' pleasantries were gated.
    assert len([e for e in client.read_events() if e.get("reason") == "ack_suppressed"]) == 2


# ── §17.5-3: empty-content lint (downgrade social, never kill substance) ────
def test_empty_content_lint_downgrades_pure_social(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")
    obs, _ = make_p2_obs("project_beta",
                         _turn(True, intent="inform", text="Sounds great, thanks — ping me if you need anything!"))
    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="inform", text="Deploy finished."))
    drain(obs)

    assert _beta_msgs(client) == []
    sup = [e for e in client.read_events() if e.get("reason") == "ack_suppressed"]
    assert len(sup) == 1 and sup[0]["gate"] == "empty_content_lint"


def test_empty_content_lint_never_kills_substantive_content(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")
    # Carries a new entity → must pass.
    obs, _ = make_p2_obs("project_beta",
                         _turn(True, intent="inform", text="The id field is order_id (a uuid)."),
                         consumer="b-sub")
    client.emit(new_envelope(from_="project_alpha", to="project_beta", intent="ask", text="id?"))
    drain(obs)
    assert client.r.xlen("aether:inbox:project_alpha") == 1  # delivered, not killed

    # A polite-but-substantive question (has '?') must also pass (false-kill defence).
    obs2, _ = make_p2_obs("project_beta",
                          _turn(True, intent="ask", text="Thanks — could you confirm the field name?"),
                          consumer="b-q")
    client.emit(new_envelope(from_="project_alpha", to="project_beta", intent="inform", text="ok"))
    drain(obs2)
    assert any("confirm the field name" in m["envelope"]["body"]["text"] for m in _beta_msgs(client))


# ── §17.1 is a HARD rule: the lint toggle cannot relax the ack gate ─────────
def test_ack_gate_is_hard_even_with_lint_disabled(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")
    # lint OFF → a social *inform* is delivered (lint is the optional layer)…
    obs, _ = make_p2_obs("project_beta",
                         _turn(True, intent="inform", text="Sounds great, thanks!"),
                         empty_content_lint=False)
    client.emit(new_envelope(from_="project_alpha", to="project_beta", intent="inform", text="done"))
    drain(obs)
    assert client.r.xlen("aether:inbox:project_alpha") == 1

    # …but an ack INTENT is still blocked (§17.1-1 hard rule, lint-independent).
    obs2, _ = make_p2_obs("project_beta", _turn(True, intent="ack", text="thx"),
                          empty_content_lint=False, consumer="b-ack2")
    client.emit(new_envelope(from_="project_alpha", to="project_beta", intent="inform", text="done2"))
    drain(obs2)
    assert any(e.get("reason") == "ack_suppressed" and e.get("gate") == "intent_ack"
               for e in client.read_events())


# ── §17.2/§17.3: register prompt mechanism (deterministic, structural) ──────
def test_concise_register_is_always_injected():
    env = new_envelope(from_="x", to="project_beta", intent="ask", text="q")
    parts = compose_prompt(env, "project_beta", register="concise")
    assert "ENGINEERING SERVICE" in parts.trusted_prefix
    assert "reply_needed=false" in parts.trusted_prefix       # the threshold
    assert "pressure-test" not in parts.trusted_prefix.lower()  # no critical stance


def test_critical_register_attaches_anti_sycophancy_stance():
    env = new_envelope(from_="reviewer", to="author", intent="task",
                       text="Review claim: bubble sort is fine for 1M items.")
    parts = compose_prompt(env, "author", register="critical")
    low = parts.trusted_prefix.lower()
    assert "pressure-test" in low and "adversarial" in low and "do not agree" in low
    # The stance is TRUSTED instruction — never injected from the untrusted body.
    assert "pressure-test" not in parts.untrusted_block.lower()


def test_register_policy_drives_the_prompt(make_p2_obs, client, heartbeat):
    seen = {"prompts": []}

    def capture(inv):
        seen["prompts"].append(inv.prompt)
        return ClaudeTurn(raw_text=control_json(False), session_id="s")

    obs, _ = make_p2_obs("project_beta", capture,
                         register_policy=lambda frm, to: "critical")
    client.emit(new_envelope(from_="project_alpha", to="project_beta",
                             intent="task", text="review my approach"))
    drain(obs)
    assert any("pressure-test" in p.lower() for p in seen["prompts"])


# ── register gate unit tests (conservative; no false kills) ─────────────────
def test_register_gate_and_pure_social_unit():
    assert is_pure_social("Thanks, got it!")
    assert is_pure_social("Sounds great — ping me if you need anything")
    assert not is_pure_social("The id field is order_id")           # snake_case datum
    assert not is_pure_social("Could you confirm the field name?")  # a question
    assert not is_pure_social("Status 200 returned")                # a number
    assert not is_pure_social("A insists on continuing")            # not social → keep

    from types import SimpleNamespace as NS
    assert register_gate(NS(reply_body={"intent": "ack", "text": "thx"})) == "intent_ack"
    assert register_gate(NS(reply_body={"intent": "inform", "text": "thanks!"})) == "empty_content_lint"
    assert register_gate(NS(reply_body={"intent": "result", "text": "order_id is the field"})) is None
    # lint can be turned off, but that's a separate switch from the ack rule
    assert register_gate(NS(reply_body={"intent": "inform", "text": "thanks!"}),
                         empty_content_lint=False) is None
    assert register_gate(NS(reply_body={"intent": "ack", "text": "thx"}),
                         empty_content_lint=False) == "intent_ack"
