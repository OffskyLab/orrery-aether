"""Consult another Aether project and wait for the answer — for interactive use.

This is the bridge that makes "ask project X a question and show me the reply"
work from an interactive Claude Code session (or any shell). Unlike
``send_message.py`` (fire-and-forget), ``consult`` is synchronous: it sends a
question from a TRANSIENT identity, waits for the directed reply, prints it, and
cleans up.

Why a transient identity: the reply routes back to the sender's inbox. If we sent
as the project's own id, the reply would (a) collide with that project's resident
Observatory and (b) the recipient would reject an unregistered sender. So we mint
a one-shot ``consult-<id>``, register + heartbeat it for the duration (so the
reply is delivered, not held), read its inbox, then deregister.

Prerequisite: the project you are consulting must have its Observatory running
(``python3 aether/run_observatory.py <that_project>``). You do NOT need to run an
Observatory for the side you are consulting *from*.

    python3 aether/consult.py --to genesis \
        --text "Which SpecBundle fields does your BundleParser require?"

Typical interactive use — tell Claude Code (opened in project X):
    "請跟 genesis 討論這個細節：<question>"
and have it run the command above and report the answer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, inbox_stream, make_redis
from aether.core.conn import add_redis_cli_opts, redis_cli_dict, resolve_redis_kwargs
from aether.core.clock import SystemClock
from aether.core.envelope import BROADCAST, Envelope, new_envelope
from aether.core.heartbeat import Heartbeat
from aether.core.registry import Body, Registry


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ask another Aether project and wait for the reply.")
    ap.add_argument("--to", required=True, help="recipient project_id (must have an Observatory running)")
    ap.add_argument("--text", required=True, help="the question (self-contained)")
    ap.add_argument("--intent", default="ask", choices=["ask", "inform", "task", "result"])
    ap.add_argument("--as", dest="as_id", default=None,
                    help="sender label (default: consult-<random>); reply routes here")
    ap.add_argument("--wait", type=int, default=180, help="seconds to wait for a reply")
    ap.add_argument("--max-hops", type=int, default=8)
    add_redis_cli_opts(ap)
    args = ap.parse_args(argv)

    if args.to == BROADCAST:
        sys.exit("consult is for a directed question; use send_message.py --wave for broadcasts")

    redis = make_redis(**resolve_redis_kwargs(cli=redis_cli_dict(args)))
    try:
        redis.ping()
    except Exception as e:
        sys.exit(f"cannot reach Redis: {e}")

    client = AetherClient(redis)
    registry = Registry(redis)
    heartbeat = Heartbeat(redis, ttl_seconds=60, clock=SystemClock())

    if not registry.has(args.to):
        known = ", ".join(registry.all().keys()) or "(none — is constellation synced?)"
        sys.exit(f"'{args.to}' is not a known body. Known: {known}\n"
                 f"Start its Observatory:  python3 aether/run_observatory.py {args.to}")
    if not heartbeat.is_online(args.to):
        print(f"WARNING: '{args.to}' has no live heartbeat — is its Observatory running? "
              f"(the reply will be held until it comes online)", file=sys.stderr)

    me = args.as_id or f"consult-{uuid.uuid4().hex[:8]}"
    registry.add(Body(me, "transient interactive consult session", ["consult"], inbox_stream(me)))
    heartbeat.beat(me)

    env = new_envelope(from_=me, to=args.to, intent=args.intent, text=args.text,
                       max_hops=args.max_hops)
    client.emit(env)
    client.emit_operator_action(me, "consult", conversation_id=env.conversation_id, to=args.to)
    print(f"→ asked '{args.to}' (conversation {env.conversation_id[:8]}); waiting up to {args.wait}s…",
          file=sys.stderr)

    inbox = inbox_stream(me)
    last_id = "0"
    deadline = time.time() + args.wait
    reply: Envelope | None = None
    try:
        while time.time() < deadline:
            heartbeat.beat(me)  # stay online so the reply is delivered, not held
            for entry_id, fields in redis.xrange(inbox, min=f"({last_id}" if last_id != "0" else "-"):
                last_id = entry_id
                cand = Envelope.from_json(fields["data"])
                if cand.conversation_id == env.conversation_id:
                    reply = cand
                    break
            if reply:
                break
            time.sleep(2)
    finally:
        registry.remove(me)
        heartbeat.go_offline(me)

    if reply is None:
        print(f"\n(no reply within {args.wait}s — '{args.to}' may still be working, offline, or "
              f"chose not to reply. conversation_id={env.conversation_id})")
        sys.exit(2)

    print(f"\n=== reply from {reply.from_} (hop {reply.hop_count}, intent {reply.body.intent}) ===")
    print(reply.body.text)


if __name__ == "__main__":
    main()
