"""Phase 2 end-to-end demo with REAL ``claude -p`` (spec §14.3).

A multi-hop, registry-routed conversation showing the Phase 2 machinery live:
  · an operator task lands in project_alpha;
  · A's real Claude CHOOSES a recipient from the injected registry (route choice);
  · the Comet is validated + delivered to the online backend Body;
  · B answers from its own docs;
  · A RESUMES its own session to read the answer and concludes;
  · the whole thing converges within Horizon, rebuilt from aether:events.

Run (docker Redis up):  python3 aether/demo_phase2.py
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, make_redis
from aether.core.clock import SystemClock
from aether.core.envelope import new_envelope
from aether.core.guards import RateLimiter
from aether.core.heartbeat import Heartbeat
from aether.core.processing_log import ProcessingLog
from aether.core.registry import Body, Registry
from aether.core.session_store import SessionStore
from aether.observatory.claude_runner import RealClaudeRunner
from aether.observatory.main import Observatory

DEMO_DB = int(os.environ.get("AETHER_DEMO_REDIS_DB", "0"))
DIRS = {"project_alpha": "/tmp/aether-demo2/project_alpha",
        "project_beta": "/tmp/aether-demo2/project_beta"}


def _live(project_id, conversation_id, evt):
    t = evt.get("type")
    if t == "system" and evt.get("subtype") == "init":
        print(f"   · [{project_id}] claude session {evt.get('session_id','')[:8]} started")
    elif t == "result":
        print(f"   · [{project_id}] claude turn done")


def _seed():
    for d in DIRS.values():
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(DIRS["project_beta"], "orders_api.md"), "w") as f:
        f.write("# Orders API (project_beta)\n\nThe orders API's unique identifier "
                "field is `order_id` (a UUID string).\n")


def main():
    _seed()
    redis = make_redis(db=DEMO_DB)
    redis.flushdb()
    client = AetherClient(redis)

    registry = Registry(redis)
    registry.sync({
        "project_alpha": Body("project_alpha", "frontend & design system", ["ui", "react"],
                              "aether:inbox:project_alpha", DIRS["project_alpha"]),
        "project_beta": Body("project_beta", "backend orders API & database", ["api", "db"],
                             "aether:inbox:project_beta", DIRS["project_beta"]),
        "project_gamma": Body("project_gamma", "data & analytics", ["etl", "reports"],
                              "aether:inbox:project_gamma", None),
    })
    hb = Heartbeat(redis, ttl_seconds=600, clock=SystemClock())
    for p in ("project_alpha", "project_beta", "project_gamma"):
        hb.beat(p)

    runner = RealClaudeRunner(event_sink=_live)

    def build(pid):
        return Observatory(
            pid, client, runner,
            RateLimiter(redis=redis, max_per_window=30, clock=SystemClock()),
            ProcessingLog(redis), session_store=SessionStore(redis, pid),
            registry=registry, heartbeat=hb, working_dir=DIRS[pid])

    obs_a, obs_b = build("project_alpha"), build("project_beta")

    print("=== Aether Phase 2 · real claude e2e (routing + session resume) ===\n")
    print("Registry (injected so A can self-route): project_alpha, project_beta, project_gamma\n")
    cid = str(uuid.uuid4())
    print(f"[operator] task → project_alpha (conversation {cid[:8]})")
    print("    'Find the orders API unique-id field name; ask the right Body.'\n")
    client.emit(new_envelope(
        from_="operator", to="project_alpha", intent="task",
        text=("You are project_alpha (frontend). To render an order-summary card you need the "
              "exact JSON field name your backend's orders API returns for an order's unique "
              "identifier. Choose the most suitable Body from the ones you may contact and send "
              "them a question asking for that field name."),
        conversation_id=cid))

    # pump
    for _round in range(12):
        moved = 0
        for obs in (obs_a, obs_b):
            while True:
                n = obs.poll_once(block_ms=300, count=10)
                if n == 0:
                    break
                moved += n
        if moved == 0:
            break

    print("\n--- Conversation timeline (rebuilt from aether:events) ---")
    max_hop = 0
    for e in client.read_events():
        k = e["kind"]
        if k == "message":
            env = e["envelope"]
            max_hop = max(max_hop, env["hop_count"])
            print(f"  msg  hop {env['hop_count']}: {env['from']} → {env['to']} "
                  f"[{env['body']['intent']}] {env['body']['text'][:70]}")
        elif k == "processing_done":
            print(f"  done [{e['to']}] {e.get('summary','')[:70]}")
        elif k in ("reply_rejected", "held", "terminated", "malformed_output"):
            print(f"  {k.upper()} reason={e.get('reason')} to={e.get('to')}")

    print("\n--- Session map (conversation_id → local session_id, per Body) ---")
    for pid in ("project_alpha", "project_beta"):
        sid = SessionStore(redis, pid).get(cid)
        turns = [i for i in runner.invocations if i.project_id == pid and i.conversation_id == cid]
        resumes = [i for i in turns if i.resume]
        print(f"  {pid}: session {str(sid)[:8]} · {len(turns)} turn(s) · {len(resumes)} resume(s)")

    print(f"\nMax hop reached: {max_hop} (Horizon=8). "
          f"{'Converged naturally within Horizon.' if max_hop < 8 else 'Hit Horizon.'}")


if __name__ == "__main__":
    main()
