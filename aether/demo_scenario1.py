"""End-to-end demo of Scenario 1 with the REAL ``claude -p`` (spec §11.3).

A (project_alpha) asks B (project_beta) a question → B answers via a real Claude
call → A reads the answer via a real Claude call and decides it's resolved
(reply_needed:false) → the conversation ends naturally, well within Horizon.

Run (with the docker Redis up):
    python3 aether/demo_scenario1.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, inbox_stream, make_redis
from aether.core.clock import SystemClock
from aether.core.envelope import new_envelope
from aether.core.guards import Dedup, RateLimiter
from aether.observatory.claude_runner import RealClaudeRunner
from aether.observatory.main import Observatory

DEMO_DB = int(os.environ.get("AETHER_DEMO_REDIS_DB", "0"))
WORKDIRS = {
    "project_alpha": "/tmp/aether-demo/project_alpha",
    "project_beta": "/tmp/aether-demo/project_beta",
}


def _live(project_id, conversation_id, evt):
    """Tiny 'Live Telescope': surface what each Body's Claude is doing."""
    t = evt.get("type")
    if t == "system" and evt.get("subtype") == "init":
        print(f"   · [{project_id}] claude session {evt.get('session_id', '')[:8]} started")
    elif t == "result":
        print(f"   · [{project_id}] claude turn done ({evt.get('subtype')})")


def build(project_id, redis, runner):
    return Observatory(
        project_id, AetherClient(redis), runner,
        RateLimiter(redis=redis, max_per_window=30, window_seconds=60, clock=SystemClock()),
        Dedup(redis=redis),
        working_dir=WORKDIRS[project_id],
    )


def drain(obs, block_ms=200):
    total = 0
    while True:
        n = obs.poll_once(block_ms=block_ms, count=10)
        if n == 0:
            return total
        total += n


def pump(observatories, max_rounds=20):
    for rounds in range(1, max_rounds + 1):
        if sum(drain(o) for o in observatories) == 0:
            return rounds
    raise SystemExit("demo did not converge (Horizon would still have stopped it)")


def _seed_body_b():
    """Make project_beta a realistic backend Body that actually owns the answer,
    so the demo shows the canonical §9 flow (B answers authoritatively → A
    concludes) rather than two empty sandboxes guessing."""
    path = os.path.join(WORKDIRS["project_beta"], "orders_api.md")
    with open(path, "w") as f:
        f.write(
            "# Orders API (project_beta)\n\n"
            "GET /api/orders/{id} returns an order object. The unique identifier\n"
            "field in the JSON response is `order_id` (a UUID string).\n"
        )


def main():
    for d in WORKDIRS.values():
        os.makedirs(d, exist_ok=True)
    _seed_body_b()

    redis = make_redis(db=DEMO_DB)
    redis.flushdb()
    client = AetherClient(redis)

    runner = RealClaudeRunner(event_sink=_live)
    obs_a = build("project_alpha", redis, runner)
    obs_b = build("project_beta", redis, runner)

    print("=== Aether Scenario 1 · real claude -p end-to-end demo ===\n")
    ask = new_envelope(
        from_="project_alpha", to="project_beta", intent="ask",
        text=(
            "I am the frontend project (project_alpha). I'm rendering an order "
            "summary card and need to know the exact JSON field name your orders "
            "API returns for an order's unique identifier. What is that field name?"
        ),
    )
    print(f"[A] emits ask → B (conversation {ask.conversation_id[:8]}, hop {ask.hop_count})")
    print(f"    Q: {ask.body.text}\n")
    client.emit(ask)

    rounds = pump([obs_a, obs_b])

    print("\n--- Conversation timeline (rebuilt from aether:events) ---")
    max_hop = 0
    for e in client.read_events():
        kind = e["kind"]
        if kind == "message":
            env = e["envelope"]
            max_hop = max(max_hop, env["hop_count"])
            print(f"  msg   hop {env['hop_count']}: {env['from']} → {env['to']} "
                  f"[{env['body']['intent']}] {env['body']['text'][:80]}")
        elif kind == "processing_done":
            print(f"  done  [{e['to']}] summary: {e.get('summary', '')[:80]}")
        elif kind == "terminated":
            print(f"  STOP  reason={e['reason']} at hop {e.get('hop_count')}")

    print(f"\nConverged in {rounds} pump round(s). Max hop reached: {max_hop} "
          f"(Horizon max_hops={ask.max_hops}). "
          f"{'OK — ended naturally within Horizon.' if max_hop < ask.max_hops else 'WARN — hit Horizon.'}")


if __name__ == "__main__":
    main()
