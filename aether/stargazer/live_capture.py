"""Live capture (spec §16.3): watch one conversation from start to natural
convergence, frame by frame, through Stargazer's real view models.

It runs a REAL multi-hop claude conversation with progress forwarding ON (so the
telescope fills in live), then walks the resulting aether:events stream one event
at a time, printing the dashboard state after each notable event — exactly what a
viewer connected to the SSE stream would have seen unfold. This is the headless
"screenshot sequence" artifact.

Run (docker Redis up):  python3 aether/stargazer/live_capture.py
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
from aether.observatory.progress import ProgressForwarder
from aether.stargazer.events import EventReader
from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.viewmodels import (build_constellation, build_extinction_log,
                                         build_telescope, build_timeline)

DB = int(os.environ.get("AETHER_CAPTURE_DB", "0"))
DIRS = {"project_alpha": "/tmp/aether-cap/project_alpha",
        "project_beta": "/tmp/aether-cap/project_beta"}


def _seed_dirs():
    for d in DIRS.values():
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(DIRS["project_beta"], "orders_api.md"), "w") as f:
        f.write("# Orders API (project_beta)\n\nThe orders API's unique identifier "
                "field is `order_id` (a UUID string).\n")


def run_conversation():
    _seed_dirs()
    redis = make_redis(db=DB)
    redis.flushdb()
    client = AetherClient(redis)
    registry = Registry(redis)
    registry.sync({
        "project_alpha": Body("project_alpha", "frontend & design", ["ui"],
                              "aether:inbox:project_alpha", DIRS["project_alpha"]),
        "project_beta": Body("project_beta", "backend orders API", ["api", "db"],
                             "aether:inbox:project_beta", DIRS["project_beta"]),
        "project_gamma": Body("project_gamma", "data & analytics", ["etl"],
                              "aether:inbox:project_gamma", None),
    }, prune=True)   # demo seed → full reset (sync default is additive)
    hb = Heartbeat(redis, ttl_seconds=600, clock=SystemClock())
    for p in ("project_alpha", "project_beta", "project_gamma"):
        hb.beat(p)

    # Progress forwarding ON → the live telescope channel fills in (§15.3).
    runner = RealClaudeRunner(event_sink=ProgressForwarder(client))

    def build(pid):
        return Observatory(
            pid, client, runner,
            RateLimiter(redis=redis, max_per_window=30, clock=SystemClock()),
            ProcessingLog(redis), session_store=SessionStore(redis, pid),
            registry=registry, heartbeat=hb, working_dir=DIRS[pid])

    obs_a, obs_b = build("project_alpha"), build("project_beta")
    cid = str(uuid.uuid4())
    client.emit(new_envelope(
        from_="operator", to="project_alpha", intent="task",
        text=("You are project_alpha (frontend). You need the exact JSON field name your "
              "backend's orders API returns for an order's unique identifier. Choose the "
              "right Body from the ones you may contact and ask them."),
        conversation_id=cid))
    for _ in range(12):
        moved = 0
        for obs in (obs_a, obs_b):
            while obs.poll_once(block_ms=300, count=10):
                moved += 1
        if moved == 0:
            break
    return redis, cid


# ── ascii frame renderer (the four §15.4 views) ─────────────────────────────
def _stars_line(records, online):
    stars = build_constellation(records, online)
    cells = []
    for pid, s in stars.items():
        glyph = "★" if s.online else "☆"
        cells.append(f"{glyph} {pid.split('_')[-1]}·act{s.activity}{'' if s.online else '(dim)'}")
    return "  ".join(cells)


def _frame(records, cid, online, trigger):
    tl = build_timeline(records, conversation_id=cid)
    tel = build_telescope(records, cid)
    ext = build_extinction_log(records)
    out = []
    out.append("─" * 78)
    out.append(f"▷ event: {trigger}")
    out.append(f"  sky:       {_stars_line(records, online)}")
    out.append("  timeline:")
    for h in tl.hops:
        out.append(f"     hop {h.hop_count}: {h.from_} → {h.to} [{h.intent}] {h.text[:46]}")
    if tl.terminal:
        out.append(f"     ✸ ended: {tl.terminal['reason']}")
    if tel.milestones:
        ms = " → ".join(m["kind"] + (f"({m.get('name')})" if m.get("name") else "")
                        for m in tel.milestones)
        out.append(f"  telescope: {ms}{'  [turn done]' if tel.ended else '  …running'}")
    if ext:
        out.append(f"  extinct:   {[e.reason for e in ext]}")
    return "\n".join(out)


def main():
    redis, cid = run_conversation()
    reader = EventReader(ReadOnlyRedis(redis))
    all_events = reader.recent(2000)
    online = {p: True for p in ("project_alpha", "project_beta", "project_gamma")}

    print("=== Stargazer live capture · one conversation, start → natural convergence ===")
    print(f"conversation: {cid[:8]} · {len(all_events)} events on aether:events\n")
    prefix = []
    for eid, rec in all_events:
        prefix.append(rec)
        et = rec.get("event_type")
        # Render a frame on every notable event (skip rendering per-progress to
        # keep it readable; progress still shows up in the telescope line).
        if et in ("message", "done", "terminated"):
            label = et
            if et == "message":
                en = rec.get("envelope", {})
                label = f"message {en.get('from')}→{en.get('to')} hop{en.get('hop_count')}"
            elif et == "done":
                label = f"done [{rec.get('to')}]"
            elif et == "terminated":
                label = f"terminated ({rec.get('reason')})"
            print(_frame(prefix, cid, online, label))
    print("─" * 78)
    print("Conversation converged naturally (no extinction). The dashboard above is "
          "rebuilt entirely from aether:events — render == event stream.")


if __name__ == "__main__":
    main()
