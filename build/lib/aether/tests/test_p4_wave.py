"""Phase 4 · Wave broadcast acceptance (spec §19.1-1..4, §18.1).

Fan-out anti-explosion is the theme: an announcement Wave must NOT amplify; a
solicited Wave must converge to N directed Comets back to the originator (never
re-broadcast); Wave-derived branches still die at Horizon; an offline body still
receives a Wave once it reconnects. All deterministic (FakeClaudeRunner).
"""
from aether.core.envelope import BROADCAST, new_envelope
from .harness import always_reply, control_json, never_reply, pump
from aether.observatory.claude_runner import ClaudeTurn


def _reply_turn(text="ack-with-substance: status ok 200"):
    # A responder that WANTS to reply (so only the Wave policy can stop it).
    return lambda inv: ClaudeTurn(raw_text=control_json(True, intent="inform", text=text),
                                  session_id="s")


def _broadcast(client, from_, text, *, solicit, max_hops=8, cid=None):
    env = new_envelope(from_=from_, to=BROADCAST, intent="inform", text=text,
                       solicit=solicit, max_hops=max_hops, conversation_id=cid)
    client.emit(env)
    return env


def _bodies(client):
    return [e for e in client.read_events() if e["event_type"] == "message"]


# ── §19.1-1: a default Wave announcement does NOT amplify ───────────────────
def test_wave_announcement_does_not_amplify(make_p2_obs, client, r):
    # 3 responder bodies, each scripted to WANT to reply — only the Wave
    # announcement policy (reply_needed=false) can stop them.
    responders = []
    for pid in ("project_beta", "project_gamma", "project_delta"):
        obs, _ = make_p2_obs(pid, _reply_turn(), subscribe_broadcast=True, consumer=pid)
        responders.append(obs)

    wave = _broadcast(client, "project_alpha", "Deploy v2 is live.", solicit=False)
    pump(responders)

    # each body processed the Wave exactly once …
    done = [e for e in client.read_events() if e["event_type"] == "done"]
    assert len(done) == 3
    assert {e["project"] for e in done} == {"project_beta", "project_gamma", "project_delta"}
    # … and produced ZERO reply Comets (the only message is the Wave itself).
    msgs = _bodies(client)
    assert len(msgs) == 1 and msgs[0]["envelope"]["type"] == "wave"
    assert all(e["envelope"]["from"] == "project_alpha" for e in msgs)


# ── §19.1-2: a solicited Wave is bounded and never re-broadcasts ────────────
def test_solicited_wave_replies_are_directed_comets(make_p2_obs, client, r, heartbeat):
    heartbeat.beat("project_alpha")  # originator online to collect
    responders = []
    for pid in ("project_beta", "project_gamma", "project_delta"):
        obs, _ = make_p2_obs(pid, _reply_turn(f"{pid} reports: 200 ok"),
                             subscribe_broadcast=True, consumer=pid)
        responders.append(obs)

    cid = "wave-conv-1"
    _broadcast(client, "project_alpha", "Report your health.", solicit=True, cid=cid)
    pump(responders)

    replies = [e for e in _bodies(client) if e["envelope"]["type"] != "wave"]
    # exactly N directed Comets, all back to the originator, none re-broadcast
    assert len(replies) == 3
    assert all(e["envelope"]["to"] == "project_alpha" for e in replies)
    assert all(e["envelope"]["type"] == "comet" for e in replies)
    assert all(e["envelope"]["to"] != BROADCAST for e in replies)
    # shared conversation_id across the whole fan-out
    assert all(e["envelope"]["conversation_id"] == cid for e in replies)
    # they physically landed in the originator's inbox, not on the broadcast stream
    assert r.xlen("aether:inbox:project_alpha") == 3
    # nobody emitted a Wave as a reply
    assert [e for e in client.read_events()
            if e.get("reason") == "wave_reply_forbidden"] == []


def test_solicited_wave_response_is_capped_by_rate_limit(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")
    # rate cap of 2 on the shared conversation → the 3rd fan-out reply is gated.
    responders = []
    for pid in ("project_beta", "project_gamma", "project_delta"):
        obs, _ = make_p2_obs(pid, _reply_turn(f"{pid}: ok"), subscribe_broadcast=True,
                             consumer=pid, max_per_window=2)
        responders.append(obs)
    cid = "wave-conv-rate"
    _broadcast(client, "project_alpha", "Report.", solicit=True, cid=cid)
    pump(responders)
    # rate limiter keyed on the shared conversation is the natural fan-out floor.
    limited = [e for e in client.read_events() if e.get("reason") == "rate_limited"]
    assert len(limited) >= 1


# ── §19.1-3: Wave-derived branch still dies at Horizon ──────────────────────
def test_wave_fanout_branch_converges_at_horizon(make_p2_obs, client, heartbeat):
    # A solicited Wave to one responder that always replies; the originator also
    # always replies → the branch ping-pongs and must stop at max_hops.
    heartbeat.beat("project_alpha"); heartbeat.beat("project_beta")
    obs_b, _ = make_p2_obs("project_beta", always_reply(text="beta keeps going"),
                           subscribe_broadcast=True, consumer="b")
    obs_a, _ = make_p2_obs("project_alpha", always_reply(text="alpha keeps going"),
                           subscribe_broadcast=False, consumer="a")
    _broadcast(client, "project_alpha", "Start a loop.", solicit=True, max_hops=4,
               cid="wave-horizon")
    pump([obs_b, obs_a])

    term = [e for e in client.read_events()
            if e["event_type"] == "terminated" and e.get("reason") == "horizon"]
    assert len(term) == 1
    assert term[0]["hop_count"] == 4
    # the fan-out branch is just Comets after hop 0; max hop equals max_hops
    hops = [e["envelope"]["hop_count"] for e in _bodies(client)]
    assert max(hops) == 4


# ── §19.1-4: an offline body still receives a Wave after reconnect ──────────
def test_offline_body_receives_wave_after_reconnect(make_p2_obs, client, r):
    # gamma's broadcast group exists (its Observatory was constructed) but it does
    # NOT poll during the Wave (offline). beta processes immediately.
    obs_beta, _ = make_p2_obs("project_beta", never_reply(), subscribe_broadcast=True,
                              consumer="beta")
    obs_gamma, _ = make_p2_obs("project_gamma", never_reply(), subscribe_broadcast=True,
                               consumer="gamma")

    _broadcast(client, "project_alpha", "Maintenance window tonight.", solicit=False)
    pump([obs_beta])  # only beta is online right now

    done_now = [e for e in client.read_events() if e["event_type"] == "done"]
    assert {e["project"] for e in done_now} == {"project_beta"}  # gamma hasn't seen it

    # gamma reconnects and polls → the buffered Wave is delivered to its group.
    pump([obs_gamma])
    done_after = [e for e in client.read_events() if e["event_type"] == "done"]
    assert {e["project"] for e in done_after} == {"project_beta", "project_gamma"}
