"""Live event feeder for testing Stargazer together (spec §16).

Emits REAL §15.1-contract events into a Redis DB with small real-time delays, so
that a Stargazer server reading the same DB pushes them to the browser live and
you can watch the four views react. No real claude needed — these are scripted,
deterministic event streams (exactly what §16.2 endorses for testing).

Usage:
    python3 -m aether.stargazer.demo_feed <scenario>
scenarios: converge | horizon | rate | dedup | ack | offline | tour
The server and this feeder must share the same DB (default 3).
"""
from __future__ import annotations

import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from aether.core.aether_client import AetherClient, make_redis
from aether.core.clock import SystemClock
from aether.core.envelope import make_reply, new_envelope
from aether.core.heartbeat import Heartbeat
from aether.core.registry import Body, Registry

DB = int(os.environ.get("AETHER_FEED_DB", "3"))
D = float(os.environ.get("AETHER_FEED_DELAY", "1.2"))  # seconds between beats

BODIES = {
    "project_alpha": Body("project_alpha", "frontend & design system", ["ui", "react"],
                          "aether:inbox:project_alpha"),
    "project_beta": Body("project_beta", "backend orders API & database", ["api", "db"],
                         "aether:inbox:project_beta"),
    "project_gamma": Body("project_gamma", "data & analytics", ["etl", "reports"],
                          "aether:inbox:project_gamma"),
}


def _setup(redis):
    Registry(redis).sync(BODIES, prune=True)   # demo seed → full reset (sync default is additive)
    hb = Heartbeat(redis, ttl_seconds=300, clock=SystemClock())
    for p in BODIES:
        hb.beat(p)
    return AetherClient(redis), hb


def _pause():
    time.sleep(D)


def _turn(client, env, project, tools=()):
    """Simulate one Claude turn's telescope milestones + done summary."""
    cid = env.conversation_id
    client.emit_progress(cid, project, "turn_start"); _pause()
    for t in tools:
        client.emit_progress(cid, project, "tool_use", name=t); _pause()
    client.emit_progress(cid, project, "turn_done", subtype="success")
    client.emit_event("processing_done", env, summary=f"{project} processed the message")
    _pause()


def converge(client):
    print("▶ scenario: natural convergence (operator → alpha → beta → answer → done)")
    cid = str(uuid.uuid4())
    task = new_envelope(from_="operator", to="project_alpha", intent="task",
                        text="Find the orders API unique-id field name; ask the right Body.",
                        conversation_id=cid)
    client.emit(task); _pause()
    _turn(client, task, "project_alpha", tools=["Glob", "Read"])

    ask = make_reply(task, from_="project_alpha", to="project_beta", intent="ask",
                     text="What JSON field is an order's unique identifier?")
    client.emit(ask); _pause()
    _turn(client, ask, "project_beta", tools=["Read"])

    ans = make_reply(ask, from_="project_beta", to="project_alpha", intent="result",
                     text="The unique identifier field is `order_id` (a uuid string).")
    client.emit(ans); _pause()
    _turn(client, ans, "project_alpha")
    print("  ✓ converged naturally within Horizon (no extinction)")


def horizon(client):
    print("▶ scenario: Horizon extinction (forced ping-pong hits the ceiling)")
    cid = str(uuid.uuid4())
    env = new_envelope(from_="project_alpha", to="project_beta", intent="ask",
                       text="ping (both sides scripted to never stop)", conversation_id=cid,
                       max_hops=4)
    client.emit(env); _pause()
    cur = env
    while cur.hop_count < cur.max_hops:
        frm = "project_beta" if cur.from_ == "project_alpha" else "project_alpha"
        to = "project_alpha" if frm == "project_beta" else "project_beta"
        nxt = make_reply(cur, from_=frm, to=to, intent="inform", text="still going")
        if nxt.hop_count >= nxt.max_hops:
            client.emit_event("terminated", nxt, reason="horizon"); _pause()
            break
        client.emit(nxt); _pause()
        cur = nxt
    print("  ✸ extinguished: horizon")


def rate(client):
    print("▶ scenario: rate-limit extinction")
    cid = str(uuid.uuid4())
    for i in range(3):
        env = new_envelope(from_="project_alpha", to="project_beta", intent="inform",
                           text=f"burst {i}", conversation_id=cid)
        client.emit(env); _pause()
    client.emit_event("terminated", env, reason="rate_limited")
    print("  ✸ extinguished: rate")


def dedup(client):
    print("▶ scenario: dedup extinction")
    env = new_envelope(from_="project_alpha", to="project_beta", intent="inform",
                       text="duplicate delivery")
    client.emit(env); _pause()
    client.emit_event("duplicate_skipped", env, reason="dedup")
    print("  ✸ extinguished: dedup")


def ack(client):
    print("▶ scenario: ack_suppressed (§17 anti-pleasantry gate)")
    env = new_envelope(from_="project_beta", to="project_alpha", intent="inform",
                       text="The deploy is green.")
    client.emit(env); _pause()
    # alpha wants to thank → gate suppresses it
    client.emit_event("ack_suppressed", env, reason="ack_suppressed",
                      to="project_beta", gate="intent_ack")
    print("  ✸ suppressed: ack_suppressed (a 'thanks' never left the station)")


def offline(client, hb):
    hold = float(os.environ.get("AETHER_OFFLINE_HOLD", "7"))
    print("▶ scenario: a star goes offline, then recovers")
    print(f"  · project_gamma heartbeat expiring → it should dim for ~{hold:.0f}s")
    hb.go_offline("project_gamma")
    time.sleep(hold)
    print("  · project_gamma heartbeat restored → it should brighten")
    hb.beat("project_gamma")


def tour(client, hb):
    converge(client); _pause()
    horizon(client); _pause()
    rate(client); _pause()
    ack(client); _pause()
    offline(client, hb)


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "converge"
    redis = make_redis(db=DB)
    client, hb = _setup(redis)
    print(f"feeding scenario '{scenario}' into db {DB} (delay {D}s) — watch http://127.0.0.1:8765\n")
    if scenario == "converge": converge(client)
    elif scenario == "horizon": horizon(client)
    elif scenario == "rate": rate(client)
    elif scenario == "dedup": dedup(client)
    elif scenario == "ack": ack(client)
    elif scenario == "offline": offline(client, hb)
    elif scenario == "tour": tour(client, hb)
    else:
        print(f"unknown scenario: {scenario}"); sys.exit(1)
    print("\ndone.")


if __name__ == "__main__":
    main()
