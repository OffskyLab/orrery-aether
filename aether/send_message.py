"""Kick off a conversation between projects from the command line.

A thin operator-inject: emits the first Comet (or a Wave) as the operator and
audits it, so a human can start a cross-project conversation without the HTTP
panel. The receiving Observatory still treats the body as untrusted data (§18.2).

    # ask project_beta a question (directed Comet)
    python3 aether/send_message.py --to project_beta \
        --text "What JSON field is an order's unique identifier?"

    # broadcast an announcement to everyone (Wave; no replies expected)
    python3 aether/send_message.py --wave --text "Deploying v2 at 02:00 UTC."

    # broadcast that explicitly asks for replies (each body answers the sender)
    python3 aether/send_message.py --wave --solicit --intent ask \
        --text "Report your current build status."
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, make_redis
from aether.core.control import ControlPlane
from aether.core.envelope import BROADCAST
from aether.operator_panel.control_service import OperatorService


def main(argv=None):
    ap = argparse.ArgumentParser(description="Send the first message of a conversation.")
    ap.add_argument("--to", help="recipient project_id (omit when using --wave)")
    ap.add_argument("--text", required=True, help="self-contained message body")
    ap.add_argument("--intent", default="ask", choices=["ask", "inform", "task", "result"])
    ap.add_argument("--from", dest="from_", default="operator", help="sender identity")
    ap.add_argument("--conversation-id", default=None)
    ap.add_argument("--wave", action="store_true", help="broadcast to all bodies")
    ap.add_argument("--solicit", action="store_true", help="(Wave) ask for replies")
    ap.add_argument("--max-hops", type=int, default=8)
    ap.add_argument("--redis-host", default=os.environ.get("AETHER_REDIS_HOST", "localhost"))
    ap.add_argument("--redis-port", type=int, default=int(os.environ.get("AETHER_REDIS_PORT", "6379")))
    ap.add_argument("--redis-db", type=int, default=int(os.environ.get("AETHER_REDIS_DB", "0")))
    args = ap.parse_args(argv)

    to = BROADCAST if args.wave else args.to
    if not to:
        ap.error("either --to <project_id> or --wave is required")
    if args.solicit and not args.wave:
        ap.error("--solicit only applies to a --wave")

    redis = make_redis(host=args.redis_host, port=args.redis_port, db=args.redis_db)
    redis.ping()
    svc = OperatorService(AetherClient(redis), ControlPlane(redis), actor=args.from_)
    result = svc.inject(to=to, intent=args.intent, text=args.text,
                        conversation_id=args.conversation_id, solicit=args.solicit,
                        max_hops=args.max_hops)

    kind = "Wave" + ("(solicit)" if args.solicit else "(announcement)") if args.wave else "Comet"
    print(f"sent {kind} from {args.from_} → {to}")
    print(f"  conversation_id: {result['conversation_id']}")
    print(f"  message_id:      {result['message_id']}")
    print("  watch it flow at http://127.0.0.1:8765 (if Stargazer is running)")


if __name__ == "__main__":
    main()
