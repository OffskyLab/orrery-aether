"""Aether MCP server — talk to other projects from inside Claude Code (spec §4/§18).

Registering this as an MCP server (via .mcp.json) gives an interactive Claude Code
session a set of tools to reach other Aether projects — no CLAUDE.md edits, no
manual CLI. The server represents YOUR session as a transient bus identity; it
runs NO headless claude (your interactive session is the brain on this side).
Only the PEER you consult needs its Observatory running, and that peer stays
read-only — so this is safe.

Two interaction modes + operator control, all async (send → poll):

  aether_list_bodies()                       — who can I talk to + who is online
  aether_ask(to, question, thread?)          — CONSULTANT: ask a peer, threaded
  aether_poll(thread)                        — pick up replies + status
  aether_discuss(from_project, to_project,…) — AUTONOMOUS: two running Observatories
  aether_transcript(thread)                  — rebuild a thread's full timeline
  aether_control(thread, action)             — OPERATOR: pause | resume | terminate

Launch (Claude Code does this from .mcp.json):
  python3 aether/mcp_server.py --identity <my-session-id>
"""
from __future__ import annotations

import argparse
import atexit
import os
import sys
import threading
import uuid
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import (AetherClient, EVENTS_STREAM, inbox_stream,
                                       make_redis)
from aether.core.clock import SystemClock
from aether.core.control import ControlPlane
from aether.core.envelope import BROADCAST, Envelope, new_envelope
from aether.core.heartbeat import Heartbeat
from aether.core.registry import Body, Registry, load_constellation
from aether.operator_panel.control_service import OperatorService
from aether.stargazer.viewmodels import build_timeline

DEFAULT_CONSTELLATION = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "constellation.yaml")


def _events_tail(redis, count=1000):
    """Bounded read of recent aether:events (chronological) — for status/transcript."""
    import json
    rows = redis.xrevrange(EVENTS_STREAM, count=count)
    return [json.loads(f["data"]) for _id, f in reversed(rows)]


class AetherBridge:
    """All the bus logic, decoupled from MCP transport (so it is unit-testable)."""

    def __init__(self, redis, identity: str, constellation: Optional[str] = None):
        self.redis = redis
        self.identity = identity
        self.client = AetherClient(redis)
        self.registry = Registry(redis)
        self.heartbeat = Heartbeat(redis, ttl_seconds=60, clock=SystemClock())
        # Named control_plane (not control) so it doesn't shadow the control() tool method.
        self.control_plane = ControlPlane(redis)
        self.operator = OperatorService(self.client, self.control_plane, actor=identity)
        self._returned: dict = {}          # thread -> set of message_ids already handed out
        self._hb_stop = threading.Event()

        # Bootstrap the star chart ONLY if empty (never clobber running Observatories'
        # transient additions); then register ourselves as a transient identity.
        if constellation and not self.registry.all():
            try:
                self.registry.load_and_sync(constellation)
            except Exception:
                pass
        self.registry.add(Body(identity, f"interactive Claude Code session ({identity})",
                               ["interactive"], inbox_stream(identity)))
        self.heartbeat.beat(identity)

    # ---- lifecycle --------------------------------------------------------
    def start_heartbeat(self):
        def loop():
            while not self._hb_stop.is_set():
                try:
                    self.heartbeat.beat(self.identity)
                    # Re-add our transient Body each tick: a Registry.sync() (from
                    # `client setup` or an Observatory startup) deletes the whole
                    # registry, which would otherwise drop us and get our replies
                    # rejected as invalid_recipient. Idempotent self-heal (spec C-fix).
                    self.registry.add(Body(self.identity,
                                           f"interactive Claude Code session ({self.identity})",
                                           ["interactive"], inbox_stream(self.identity)))
                except Exception:
                    pass
                self._hb_stop.wait(10)
        threading.Thread(target=loop, daemon=True).start()

    def cleanup(self):
        self._hb_stop.set()
        try:
            self.registry.remove(self.identity)
            self.heartbeat.go_offline(self.identity)
        except Exception:
            pass

    # ---- tools ------------------------------------------------------------
    def list_bodies(self) -> dict:
        bodies = []
        for pid, b in sorted(self.registry.all().items()):
            if pid == self.identity:
                continue
            bodies.append({"id": pid, "description": b.description,
                           "capabilities": b.capabilities,
                           "online": self.heartbeat.is_online(pid)})
        return {"me": self.identity, "bodies": bodies,
                "note": "online=false means that project's Observatory isn't running; "
                        "a message to it is held until it comes online."}

    def ask(self, to: str, question: str, thread: Optional[str] = None,
            intent: str = "ask") -> dict:
        if to == BROADCAST:
            return {"error": "use aether_discuss or a Wave for broadcasts; ask is directed."}
        if not self.registry.has(to):
            return {"error": f"unknown body '{to}'", "known": list(self.registry.all())}
        env = new_envelope(from_=self.identity, to=to, intent=intent, text=question,
                           conversation_id=thread)
        self.client.emit(env)
        self.client.emit_operator_action(self.identity, "ask",
                                         conversation_id=env.conversation_id, to=to)
        online = self.heartbeat.is_online(to)
        return {
            "thread": env.conversation_id, "to": to, "status": "sent",
            "peer_online": online,
            "next": f"call aether_poll('{env.conversation_id}') in ~30-90s for the reply"
                    + ("" if online else f" — NOTE: '{to}' is offline; start its Observatory"),
        }

    def poll(self, thread: str) -> dict:
        returned = self._returned.setdefault(thread, set())
        new: List[dict] = []
        for _id, fields in self.redis.xrange(inbox_stream(self.identity)):
            env = Envelope.from_json(fields["data"])
            if env.conversation_id == thread and env.message_id not in returned:
                returned.add(env.message_id)
                new.append({"from": env.from_, "intent": env.body.intent,
                            "hop": env.hop_count, "text": env.body.text})
        tl = build_timeline(_events_tail(self.redis), conversation_id=thread)
        if tl.terminal:
            status = f"extinguished:{tl.terminal['reason']}"
        elif new:
            status = "reply_received"
        else:
            status = "waiting"  # peer may still be reading its repo
        return {"thread": thread, "status": status, "new_replies": new}

    def discuss(self, from_project: str, to_project: str, topic: str,
                solicit: bool = False) -> dict:
        for p in (from_project, to_project):
            if not self.registry.has(p):
                return {"error": f"unknown body '{p}'", "known": list(self.registry.all())}
        res = self.operator.inject(to=to_project, intent="ask", text=topic,
                                   from_=from_project, solicit=solicit)
        return {
            "thread": res["conversation_id"], "from": from_project, "to": to_project,
            "note": "AUTONOMOUS: both projects' Observatories must be running for them to "
                    "discuss back-and-forth; watch with aether_transcript(thread).",
            "next": f"aether_transcript('{res['conversation_id']}')",
        }

    def transcript(self, thread: str) -> dict:
        tl = build_timeline(_events_tail(self.redis, count=2000), conversation_id=thread)
        return {
            "thread": thread,
            "hops": [{"hop": h.hop_count, "from": h.from_, "to": h.to,
                      "intent": h.intent, "text": h.text} for h in tl.hops],
            "summaries": tl.summaries,
            "operator_actions": tl.actions,
            "ended": tl.terminal,
        }

    def control(self, thread: str, action: str) -> dict:
        action = action.lower()
        if action == "pause":
            return self.operator.pause(thread)
        if action == "resume":
            return self.operator.resume(thread)
        if action in ("terminate", "kill", "stop"):
            return self.operator.terminate(thread)
        return {"error": f"unknown action '{action}'", "valid": ["pause", "resume", "terminate"]}


# ---- MCP wiring ------------------------------------------------------------
def build_server(bridge: AetherBridge):
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("aether")

    @mcp.tool()
    def aether_list_bodies() -> dict:
        """List the other Aether projects you can talk to, with their description,
        capabilities, and whether each is currently online (its Observatory running)."""
        return bridge.list_bodies()

    @mcp.tool()
    def aether_ask(to: str, question: str, thread: str = "") -> dict:
        """Ask another project a question (CONSULTANT mode). Async: returns a
        `thread` id immediately — call aether_poll(thread) to get the reply. Pass an
        existing `thread` to continue a prior exchange (the peer remembers context).
        The question must be self-contained (the peer lacks your context)."""
        return bridge.ask(to, question, thread or None)

    @mcp.tool()
    def aether_poll(thread: str) -> dict:
        """Pick up any new replies on a thread and its status
        (waiting | reply_received | extinguished:<reason>)."""
        return bridge.poll(thread)

    @mcp.tool()
    def aether_discuss(from_project: str, to_project: str, topic: str,
                       solicit: bool = False) -> dict:
        """Start an AUTONOMOUS back-and-forth between two OTHER projects (both must
        have their Observatories running). Returns a `thread`; watch it unfold with
        aether_transcript(thread). Use this when you want two agents to hash out a
        topic on their own; use aether_ask when YOU want to drive the questions."""
        return bridge.discuss(from_project, to_project, topic, solicit)

    @mcp.tool()
    def aether_transcript(thread: str) -> dict:
        """Rebuild a conversation's full timeline from the event log: every hop
        (from→to, text), per-turn summaries, operator actions, and how it ended."""
        return bridge.transcript(thread)

    @mcp.tool()
    def aether_control(thread: str, action: str) -> dict:
        """OPERATOR control over a conversation: action = pause | resume | terminate.
        Use terminate to stop a runaway/looping discussion (manual Horizon). Audited."""
        return bridge.control(thread, action)

    # ── "/" slash commands (MCP prompts → /mcp__aether__<name>, spec C5) ──
    # READS pre-fetch live data at render (most "direct"); WRITES return a minimal
    # single-tool-call instruction and hand off to /poll — they NEVER send/terminate
    # at render time (avoids double-send) and NEVER inject a polling loop.
    _MAX = 600  # transcript text bound per hop

    @mcp.prompt()
    def who() -> str:
        """List the Aether projects you can talk to and who is online."""
        data = bridge.list_bodies()
        lines = [f"You ({data['me']}) can talk to:"]
        for b in data["bodies"]:
            lines.append(f"  - {b['id']} [{'online' if b['online'] else 'OFFLINE'}] "
                         f"{b['description']} (caps: {', '.join(b['capabilities'])})")
        lines.append(data["note"])
        return "\n".join(lines)

    @mcp.prompt()
    def ask(to: str, question: str, thread: str = "") -> str:
        """Ask another project a question (you drive). Returns an instruction to run the ask tool."""
        t = f", thread='{thread}'" if thread else ""
        return (f"Use the `aether_ask` tool with to='{to}', question='{question}'{t}. "
                f"Report the returned thread id and tell me to run `/mcp__aether__poll <thread>` "
                f"in ~30-90s to fetch the reply. Do NOT poll in a loop yourself and do NOT resend.")

    @mcp.prompt()
    def poll(thread: str) -> str:
        """Pick up replies + status on a thread (pre-fetched)."""
        data = bridge.poll(thread)
        if data.get("new_replies"):
            out = [f"thread {thread} — status {data['status']}:"]
            for rp in data["new_replies"]:
                out.append(f"  {rp['from']} (hop {rp['hop']}): {rp['text']}")
            return "\n".join(out)
        return f"thread {thread} — status {data['status']} (no new reply yet; poll again shortly)."

    @mcp.prompt()
    def discuss(from_project: str, to_project: str, topic: str) -> str:
        """Start an autonomous discussion between two projects (both must be online)."""
        return (f"Use the `aether_discuss` tool with from_project='{from_project}', "
                f"to_project='{to_project}', topic='{topic}'. Report the returned thread and tell me "
                f"to run `/mcp__aether__transcript <thread>` to watch it. Both projects' Observatories "
                f"must be running. Do NOT loop.")

    @mcp.prompt()
    def transcript(thread: str) -> str:
        """Show a conversation's full timeline (pre-fetched, bounded)."""
        data = bridge.transcript(thread)
        out = [f"transcript of {thread}:"]
        for h in data.get("hops", []):
            out.append(f"  hop {h['hop']}: {h['from']} → {h['to']} [{h['intent']}] {h['text'][:_MAX]}")
        if data.get("ended"):
            out.append(f"  ended: {data['ended'].get('reason')}")
        return "\n".join(out) if len(out) > 1 else f"no activity on thread {thread} yet."

    @mcp.prompt()
    def stop(thread: str) -> str:
        """Confirm before terminating a conversation (operator action)."""
        return (f"To terminate thread '{thread}' (manual Horizon, audited), run the "
                f"`aether_control` tool with thread='{thread}', action='terminate'. "
                f"Confirm with me first if this conversation might still be wanted.")

    return mcp


def main(argv=None):
    ap = argparse.ArgumentParser(description="Aether MCP server (talk to other projects).")
    ap.add_argument("--identity", default=os.environ.get("AETHER_MCP_IDENTITY")
                    or f"mcp-{uuid.uuid4().hex[:6]}",
                    help="this session's bus identity (replies route here)")
    ap.add_argument("--constellation", default=os.environ.get("AETHER_CONSTELLATION",
                                                              DEFAULT_CONSTELLATION))
    ap.add_argument("--redis-host", default=os.environ.get("AETHER_REDIS_HOST", "localhost"))
    ap.add_argument("--redis-port", type=int, default=int(os.environ.get("AETHER_REDIS_PORT", "6379")))
    ap.add_argument("--redis-db", type=int, default=int(os.environ.get("AETHER_REDIS_DB", "0")))
    args = ap.parse_args(argv)

    redis = make_redis(host=args.redis_host, port=args.redis_port, db=args.redis_db)
    redis.ping()
    bridge = AetherBridge(redis, args.identity, constellation=args.constellation)
    bridge.start_heartbeat()
    atexit.register(bridge.cleanup)
    build_server(bridge).run()  # stdio


if __name__ == "__main__":
    main()
