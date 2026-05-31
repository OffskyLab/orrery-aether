"""Launch a resident Observatory for ONE real project (spec §5.4).

This is the entry point that wires a real project into Aether: it loads
``constellation.yaml``, builds an Observatory for the given project_id with the
real ``claude -p`` runner (read-only tools by default, §13.6) plus all the
Phase 2–4 collaborators (idempotency log, persisted sessions, registry routing,
heartbeat, operator control plane, live-telescope progress forwarding), and runs
the receive → prompt → claude → maybe-reply loop forever.

Run one of these per project, each in its own terminal:

    python3 aether/run_observatory.py project_alpha
    python3 aether/run_observatory.py project_beta

Stop with Ctrl+C. Safety: the message-triggered Claude gets READ-ONLY tools
(Read/Glob/Grep) unless you pass --allow-write — keep it read-only for a first
real project (no human in the loop, §13.5/§13.6).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, make_redis
from aether.core.clock import SystemClock
from aether.core.control import ControlPlane
from aether.core.guards import RateLimiter
from aether.core.heartbeat import Heartbeat
from aether.core.processing_log import ProcessingLog
from aether.core.registry import Registry, load_constellation
from aether.core.session_store import SessionStore
from aether.observatory.claude_runner import RealClaudeRunner
from aether.observatory.main import Observatory
from aether.observatory.progress import ProgressForwarder

DEFAULT_CONSTELLATION = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "constellation.yaml")


def _telescope_log(project_id, conversation_id, evt):
    t = evt.get("type")
    if t == "system" and evt.get("subtype") == "init":
        print(f"   · [{project_id}] claude session {evt.get('session_id', '')[:8]} started")
    elif t == "result":
        print(f"   · [{project_id}] claude turn done ({evt.get('subtype')})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run an Aether Observatory for one project.")
    ap.add_argument("project_id", help="must match a body in constellation.yaml")
    ap.add_argument("--constellation", default=DEFAULT_CONSTELLATION)
    ap.add_argument("--redis-host", default=os.environ.get("AETHER_REDIS_HOST", "localhost"))
    ap.add_argument("--redis-port", type=int, default=int(os.environ.get("AETHER_REDIS_PORT", "6379")))
    ap.add_argument("--redis-db", type=int, default=int(os.environ.get("AETHER_REDIS_DB", "0")))
    ap.add_argument("--rate-per-min", type=int, default=20,
                    help="max messages per conversation per minute (cost backstop)")
    ap.add_argument("--block-ms", type=int, default=5000)
    ap.add_argument("--allow-write", action="store_true",
                    help="DANGER: give the triggered claude write/exec tools (default: read-only)")
    ap.add_argument("--verbatim-telescope", action="store_true",
                    help="forward full claude text to the dashboard (default: milestones only)")
    args = ap.parse_args(argv)

    bodies = load_constellation(args.constellation)
    if args.project_id not in bodies:
        sys.exit(f"'{args.project_id}' is not in {args.constellation}. "
                 f"Known bodies: {', '.join(bodies) or '(none)'}")
    cfg = bodies[args.project_id]
    working_dir = cfg.working_dir
    if working_dir and not os.path.isdir(working_dir):
        print(f"WARNING: working_dir does not exist: {working_dir}")

    redis = make_redis(host=args.redis_host, port=args.redis_port, db=args.redis_db)
    try:
        redis.ping()
    except Exception as e:
        sys.exit(f"cannot reach Redis at {args.redis_host}:{args.redis_port} db{args.redis_db}: {e}\n"
                 f"Start it:  docker compose -f aether/docker-compose.yml up -d redis")

    client = AetherClient(redis)
    Registry(redis).load_and_sync(args.constellation)  # publish the star chart
    heartbeat = Heartbeat(redis, ttl_seconds=30, clock=SystemClock())
    runner = RealClaudeRunner(
        event_sink=ProgressForwarder(client, verbatim=args.verbatim_telescope),
        read_only=not args.allow_write,
    )
    obs = Observatory(
        args.project_id, client, runner,
        RateLimiter(redis=redis, max_per_window=args.rate_per_min, window_seconds=60,
                    clock=SystemClock()),
        ProcessingLog(redis),
        session_store=SessionStore(redis, args.project_id),
        registry=Registry(redis), heartbeat=heartbeat,
        control_plane=ControlPlane(redis),
        working_dir=working_dir,
        subscribe_broadcast=True,  # also receive Waves (broadcasts)
    )

    tools = "WRITE+EXEC (dangerous)" if args.allow_write else "read-only (Read/Glob/Grep)"
    print(f"=== Observatory '{args.project_id}' online ===")
    print(f"  working_dir : {working_dir}")
    print(f"  redis       : {args.redis_host}:{args.redis_port} db{args.redis_db}")
    print(f"  claude tools: {tools}")
    print(f"  rate limit  : {args.rate_per_min}/min per conversation · Horizon caps every conversation")
    print(f"  watching for messages… (Ctrl+C to stop)\n")

    # The resident loop (spec §5.2/§5.4): heartbeat, recover crashed work, flush
    # offline/paused holds, then poll. Equivalent to Observatory.run_forever but
    # with friendly logging.
    obs.recover_pending()
    obs.flush_hold()
    try:
        while True:
            heartbeat.beat(args.project_id)
            obs.flush_hold()
            obs.flush_paused()
            n = obs.poll_once(block_ms=args.block_ms)
            if n:
                print(f"   [{args.project_id}] processed {n} message(s)")
    except KeyboardInterrupt:
        print(f"\n=== Observatory '{args.project_id}' offline ===")


if __name__ == "__main__":
    main()
