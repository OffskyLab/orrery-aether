"""Real Observatory processing — proof that the §17 register gate FIRES and the
§17.2 register fragment is injected into the actual prompt.

Unlike demo_feed.py (which writes pre-baked events straight onto aether:events),
this runs the REAL ``Observatory.process()`` pipeline — processing log, guards,
register gate, Redis — and shows:

  #2  a side that PRODUCES an ack / pure-thanks reply has it blocked
      (reply_needed downgraded to false, nothing delivered, ack_suppressed logged);
  #3  the §17.2 communication register ("no pleasantries", reply_needed threshold)
      is present in the exact prompt the Observatory handed to Claude — and sits in
      the TRUSTED section, before the untrusted external-message block.

    python3 aether/demo_register.py          # deterministic real pipeline (fake model output)
    python3 aether/demo_register.py --real    # also run one REAL claude -p turn (§17.2 live)
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, inbox_stream, make_redis
from aether.core.clock import SystemClock
from aether.core.envelope import new_envelope
from aether.core.guards import RateLimiter
from aether.core.heartbeat import Heartbeat
from aether.core.processing_log import ACKED, REPLY_EMITTED, ProcessingLog
from aether.core.registry import Body, Registry
from aether.core.session_store import SessionStore
from aether.observatory.claude_runner import ClaudeTurn, RealClaudeRunner
from aether.observatory.main import Observatory
from aether.observatory.prompt import UNTRUSTED_BEGIN

DB = int(os.environ.get("AETHER_REGISTER_DEMO_DB", "3"))


class CapturingRunner:
    """Records the prompt it is handed; returns a scripted turn, or delegates to an
    inner real runner. Lets us inspect EXACTLY what the pipeline built."""

    def __init__(self, turn=None, inner=None):
        self.turn, self.inner = turn, inner
        self.invocations, self.last_prompt = [], None

    def run(self, inv):
        self.last_prompt = inv.prompt
        self.invocations.append(inv)
        return self.inner.run(inv) if self.inner else self.turn


def _scripted(reply_needed, intent=None, text=None):
    obj = {"reply_needed": reply_needed, "to": None,
           "reply_body": ({"intent": intent, "text": text} if reply_needed else None)}
    return ClaudeTurn(raw_text=json.dumps(obj), session_id="sess-demo")


def _setup(redis):
    Registry(redis).sync({
        "project_alpha": Body("project_alpha", "frontend", ["ui"], "aether:inbox:project_alpha"),
        "project_beta": Body("project_beta", "backend orders API", ["api"], "aether:inbox:project_beta"),
    }, prune=True)   # demo seed → full reset (sync default is additive)
    hb = Heartbeat(redis, ttl_seconds=300, clock=SystemClock())
    hb.beat("project_alpha"); hb.beat("project_beta")
    return hb


def _build(redis, project, runner, working_dir=None):
    return Observatory(
        project, AetherClient(redis), runner,
        RateLimiter(redis=redis, max_per_window=100, clock=SystemClock()),
        ProcessingLog(redis), session_store=SessionStore(redis, project),
        registry=Registry(redis), heartbeat=Heartbeat(redis, ttl_seconds=300, clock=SystemClock()),
        working_dir=working_dir)


def _show_register_injection(prompt):
    print("  #3 · §17.2 register fragment present in the ACTUAL processed prompt:")
    for needle in ['You are the resident Claude agent',
                   'another ENGINEERING SERVICE, not a person',
                   'Do NOT greet, thank, compliment',
                   'reply_needed threshold',
                   'set reply_needed=false',
                   'Silence means']:
        mark = "✓" if needle in prompt else "✗"
        print(f"       [{mark}] {needle!r}")
    reg = prompt.find("COMMUNICATION REGISTER")
    unt = prompt.find(UNTRUSTED_BEGIN)
    print(f"       register block at char {reg}; untrusted block at char {unt} "
          f"→ register is TRUSTED/before-untrusted: {0 <= reg < unt}")


def _process_case(redis, client, label, runner, inbound):
    obs = _build(redis, "project_beta", runner)
    before = redis.xlen(inbox_stream("project_alpha"))
    obs.process(inbound)                                   # ← the REAL pipeline
    after = redis.xlen(inbox_stream("project_alpha"))
    evs = [e for e in client.read_events() if e.get("message_id") == inbound.message_id]
    sup = [e for e in evs if e.get("reason") == "ack_suppressed"]
    print(f"\n── {label} ──")
    print(f"  inbound  : from={inbound.from_} intent={inbound.body.intent} text={inbound.body.text!r}")
    print(f"  Claude WANTED to reply (scripted): "
          f"{json.loads(runner.invocations and runner.turn.raw_text or '{}')}")
    print(f"  #2 · outbound Comets delivered to project_alpha: {after - before}  "
          f"({'BLOCKED' if after == before else 'sent'})")
    if sup:
        print(f"       ack_suppressed logged: reason={sup[0]['reason']} gate={sup[0].get('gate')}")
    print(f"       processing log reached ACKED (turn done, no reply): "
          f"{obs.proclog.state(inbound.message_id) == ACKED}")
    return obs


def main():
    real = "--real" in sys.argv
    redis = make_redis(db=DB)
    _setup(redis)
    client = AetherClient(redis)

    print("=== REAL Observatory.process() — §17 register gate (NOT demo_feed) ===")

    # CASE A — the model emits an ACK-intent reply → §17.1-1 hard gate blocks it.
    runner_a = CapturingRunner(_scripted(True, intent="ack", text="Thanks so much, got it — really appreciate it!"))
    inbound_a = new_envelope(from_="project_alpha", to="project_beta", intent="inform",
                             text="FYI: the orders deploy is green.")
    obs_a = _process_case(redis, client, "CASE A · ack-intent reply", runner_a, inbound_a)
    _show_register_injection(runner_a.last_prompt)

    # CASE B — the model emits a pure-thanks reply tagged intent=inform → §17.1-3 lint.
    runner_b = CapturingRunner(_scripted(True, intent="inform", text="Sounds great, thanks — ping me if you need anything!"))
    inbound_b = new_envelope(from_="project_alpha", to="project_beta", intent="inform",
                             text="FYI: nightly backup completed.")
    _process_case(redis, client, "CASE B · pure-social reply (empty-content lint)", runner_b, inbound_b)

    if real:
        # CASE C — a REAL claude -p turn under the register, on a purely social inbound.
        wd = "/tmp/aether-reg/project_beta"; os.makedirs(wd, exist_ok=True)
        runner_c = CapturingRunner(inner=RealClaudeRunner())
        inbound_c = new_envelope(from_="project_alpha", to="project_beta", intent="inform",
                                 text=("Thanks a lot for the order_id answer earlier — really "
                                       "appreciate the quick help. No action needed on your side."))
        obs_c = _build(redis, "project_beta", runner_c, working_dir=wd)
        print("\n── CASE C · REAL claude -p under the §17.2 register (pure-social inbound) ──")
        obs_c.process(inbound_c)
        ctrl = obs_c.proclog.load_claude_result(inbound_c.message_id) or {}
        delivered = redis.xlen(inbox_stream("project_alpha"))
        evs = [e for e in client.read_events() if e.get("message_id") == inbound_c.message_id]
        sup = [e for e in evs if e.get("reason") == "ack_suppressed"]
        print(f"  real claude decision: reply_needed={ctrl.get('reply_needed')} "
              f"intent={(ctrl.get('reply_body') or {}).get('intent')}")
        print(f"  outbound to project_alpha total now: {delivered}; ack_suppressed for this msg: {len(sup)}")
        print("  → either the model stayed silent (§17.2 register worked) or it tried to ack "
              "and the §17.1 gate caught it; a pleasantry never rode the bus either way.")

    print("\nDB:", DB, "(watch http://127.0.0.1:8765 — these ack_suppressed events show in 熄滅紀錄)")


if __name__ == "__main__":
    main()
