"""Phase 4 demo — operator pause → resume → terminate, visible on the timeline.

Drives the REAL Observatory pipeline + the REAL operator control plane (no HTTP
needed for the action logic) and prints the Stargazer timeline rebuilt from
aether:events, showing operator actions interleaved with the conversation. Also
demos a Wave announcement (fan-out, no amplification).

Optionally feeds the live dashboard (db 3) so you can watch it:
    python3 aether/demo_phase4.py            # deterministic, prints timeline
    AETHER_PHASE4_DB=3 python3 aether/demo_phase4.py   # also lands on the dashboard
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, make_redis
from aether.core.clock import SystemClock
from aether.core.control import ControlPlane
from aether.core.envelope import BROADCAST, new_envelope
from aether.core.guards import RateLimiter
from aether.core.heartbeat import Heartbeat
from aether.core.processing_log import ProcessingLog
from aether.core.registry import Body, Registry
from aether.observatory.claude_runner import ClaudeTurn
from aether.observatory.main import Observatory
from aether.operator_panel.control_service import OperatorService
from aether.stargazer.viewmodels import build_operator_log, build_timeline

DB = int(os.environ.get("AETHER_PHASE4_DB", "9"))


def _turn_never(inv):
    import json
    return ClaudeTurn(raw_text=json.dumps({"reply_needed": False, "to": None,
                                           "reply_body": None}), session_id="s")


def _setup():
    redis = make_redis(db=DB)
    redis.flushdb()
    client = AetherClient(redis)
    Registry(redis).sync({
        "project_alpha": Body("project_alpha", "frontend", ["ui"], "aether:inbox:project_alpha"),
        "project_beta": Body("project_beta", "backend", ["api"], "aether:inbox:project_beta"),
        "project_gamma": Body("project_gamma", "data", ["etl"], "aether:inbox:project_gamma"),
    })
    hb = Heartbeat(redis, ttl_seconds=600, clock=SystemClock())
    for p in ("project_alpha", "project_beta", "project_gamma"):
        hb.beat(p)
    control = ControlPlane(redis)
    return redis, client, hb, control


def _build(redis, client, hb, control, pid, subscribe_broadcast=False):
    return Observatory(
        pid, client, FakeRunnerHolder.runner, RateLimiter(redis=redis, max_per_window=50,
                                                          clock=SystemClock()),
        ProcessingLog(redis), registry=Registry(redis), heartbeat=hb,
        control_plane=control, subscribe_broadcast=subscribe_broadcast, consumer=pid)


class FakeRunnerHolder:
    class runner:
        invocations = []
        @staticmethod
        def run(inv):
            FakeRunnerHolder.runner.invocations.append(inv)
            return _turn_never(inv)


def _drain(obs):
    while obs.poll_once(block_ms=50, count=10):
        pass


def print_timeline(client, cid, title):
    tl = build_timeline(client.read_events(), conversation_id=cid)
    print(f"\n── timeline · {title} (conversation {cid[:8]}) ──")
    # interleave hops + operator actions in event order
    for rec in client.read_events():
        if rec.get("conversation_id") != cid:
            continue
        et = rec.get("event_type")
        if et == "message":
            en = rec["envelope"]; b = en.get("body", {})
            tag = "〰wave" if en["type"] == "wave" else f"hop {en['hop_count']}"
            print(f"   msg  {tag}: {en['from']} → {en['to']} [{b.get('intent')}] {b.get('text','')[:48]}")
        elif et == "done":
            print(f"   done [{rec.get('project')}] processed")
        elif et == "operator_action":
            print(f"   ⚙ OPERATOR {rec.get('action')}"
                  + (f" ({rec.get('reason')})" if rec.get("reason") else ""))
        elif et == "terminated":
            print(f"   ✸ extinguished: {rec.get('reason')}")


def main():
    redis, client, hb, control = _setup()
    svc = OperatorService(client, control)
    beta = _build(redis, client, hb, control, "project_beta", subscribe_broadcast=True)

    print("=== Aether Phase 4 demo — Wave + operator control plane ===")

    # 1) Wave announcement: fan-out, no amplification.
    print("\n[1] operator broadcasts a Wave announcement (no reply expected)")
    wave = new_envelope(from_="operator", to=BROADCAST, intent="inform",
                        text="Scheduled maintenance at 02:00 UTC.", solicit=False)
    client.emit(wave)
    svc.client.emit_operator_action("operator", "inject", conversation_id=wave.conversation_id,
                                    to=BROADCAST, wave=True)
    _drain(beta)
    print_timeline(client, wave.conversation_id, "Wave announcement")

    # 2) operator pause → resume → terminate a conversation.
    print("\n[2] operator lifecycle on a directed conversation: pause → resume → terminate")
    cid = "demo-lifecycle"

    print("   • operator PAUSE")
    svc.pause(cid)
    client.emit(new_envelope(from_="operator", to="project_beta", intent="task",
                             text="Investigate the latency spike.", conversation_id=cid))
    _drain(beta)
    print(f"     held (not processed): inbound_hold={client.inbound_hold_len('project_beta')}")

    print("   • operator RESUME")
    svc.resume(cid)
    beta.flush_paused()
    _drain(beta)
    print("     resumed → message processed")

    print("   • operator TERMINATE (manual Horizon)")
    svc.terminate(cid)
    client.emit(new_envelope(from_="operator", to="project_beta", intent="task",
                             text="(late message after kill)", conversation_id=cid))
    _drain(beta)
    print("     terminated → in-flight message dropped, reason=operator_kill")

    print_timeline(client, cid, "operator pause→resume→terminate")

    print("\n── audit log (reconstructed from aether:events) ──")
    for a in build_operator_log(client.read_events()):
        print(f"   {a.ts}  actor={a.actor}  action={a.action}  cid={(a.conversation_id or '')[:12]}")

    print(f"\nDB {DB}. Every operator action is on the timeline AND in the audit log — "
          "the write surface is itself observable (§18.2).")


if __name__ == "__main__":
    main()
