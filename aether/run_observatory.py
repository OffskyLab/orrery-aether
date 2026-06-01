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

import redis as redis_lib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.core.aether_client import AetherClient, make_redis
from aether.core.clock import SystemClock
from aether.core.conn import resolve_redis_kwargs
from aether.core.control import ControlPlane
from aether.core.guards import RateLimiter
from aether.core.heartbeat import Heartbeat
from aether.core.processing_log import ProcessingLog
from aether.core.registry import DuplicateBodyError, Registry, load_constellation
from aether.core.session_store import SessionStore
from aether.observatory.claude_runner import RealClaudeRunner
from aether.observatory.main import Observatory
from aether.observatory.progress import ProgressForwarder

from aether.cli_support import sanitize_id

# Mutable user data → stable user-owned location (NOT the package dir, which a
# reinstall wipes). AETHER_CONSTELLATION overrides.
DEFAULT_CONSTELLATION = (os.environ.get("AETHER_CONSTELLATION")
                         or os.path.expanduser("~/.aether/constellation.yaml"))


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
    # Connection flags default to None (tri-state) → resolver applies precedence
    # flag > env > profile > default (bare command == previous behaviour).
    ap.add_argument("--redis-host", default=None)
    ap.add_argument("--redis-port", type=int, default=None)
    ap.add_argument("--redis-db", type=int, default=None)
    ap.add_argument("--redis-password", default=None)
    ap.add_argument("--redis-username", default=None)
    ap.add_argument("--redis-tls", dest="redis_tls", action="store_const", const=True, default=None)
    ap.add_argument("--redis-no-tls", dest="redis_tls", action="store_const", const=False)
    ap.add_argument("--redis-tls-ca", default=None)
    ap.add_argument("--heartbeat-ttl", type=int, default=30,
                    help="online TTL seconds; raise for high-latency cross-machine links "
                         "(> 2x worst RTT + beat interval)")
    ap.add_argument("--rate-per-min", type=int, default=20,
                    help="max messages per conversation per minute (cost backstop)")
    ap.add_argument("--block-ms", type=int, default=5000)
    ap.add_argument("--allow-write", action="store_true",
                    help="DANGER: give the triggered claude write/exec tools (default: read-only)")
    ap.add_argument("--verbatim-telescope", action="store_true",
                    help="forward full claude text to the dashboard (default: milestones only)")
    args = ap.parse_args(argv)

    # Sanitize the id the SAME way `client setup`/`register` do, so
    # `aether observatory EventStormingTool` matches the registered `eventstormingtool`
    # (they store the sanitized form). Without this the two commands disagreed.
    args.project_id = sanitize_id(args.project_id)

    if not os.path.isfile(args.constellation):
        sys.exit(f"no constellation at {args.constellation}\n"
                 f"Register this project first:  aether client setup   (or  aether register …)")
    bodies = load_constellation(args.constellation)
    if args.project_id not in bodies:
        sys.exit(f"'{args.project_id}' is not in {args.constellation}. "
                 f"Known bodies: {', '.join(bodies) or '(none)'}")
    cfg = bodies[args.project_id]
    # working_dir guard (spec C5): null = a REMOTE body, must not be started here;
    # missing dir = misconfig. Both hard-error (was a silent WARNING) so we never
    # launch claude -p in the wrong / parent cwd.
    working_dir = cfg.working_dir
    if working_dir is None:
        sys.exit(f"'{args.project_id}' has working_dir=null (marked remote / not-local) — "
                 f"do not start its Observatory on this machine.")
    if not os.path.isdir(working_dir):
        sys.exit(f"working_dir does not exist: {working_dir}\n"
                 f"Fix constellation.yaml, or start this Observatory on the machine that has the repo.")

    rk = resolve_redis_kwargs(cli={
        "host": args.redis_host, "port": args.redis_port, "db": args.redis_db,
        "password": args.redis_password, "username": args.redis_username,
        "ssl": args.redis_tls, "ssl_ca_certs": args.redis_tls_ca})
    redis = make_redis(**rk)
    try:
        redis.ping()
    except redis_lib.exceptions.AuthenticationError as e:
        sys.exit(f"Redis auth failed at {rk['host']}:{rk['port']}: {e}\n"
                 f"Set AETHER_REDIS_PASSWORD (or --redis-password) correctly.")
    except Exception as e:
        sys.exit(f"cannot reach Redis at {rk['host']}:{rk['port']} db{rk['db']}: {e}\n"
                 f"Start it:  docker compose -f aether/docker-compose.yml up -d redis")

    client = AetherClient(redis)
    # Register ONLY our own body (additive, fail-closed) — do NOT load_and_sync the
    # whole local constellation (that would re-register peers and可能撞 conflict).
    try:
        Registry(redis).register_body(cfg)
    except DuplicateBodyError as e:
        sys.exit(f"{e}\n(Your local constellation conflicts with what's already on the bus "
                 f"for '{args.project_id}'. Trim it to only your own body, or use a different id.)")
    heartbeat = Heartbeat(redis, ttl_seconds=args.heartbeat_ttl, clock=SystemClock())
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
    tls = " TLS" if rk.get("ssl") else ""
    auth = " +auth" if rk.get("password") else ""
    print(f"=== Observatory '{args.project_id}' online ===")
    print(f"  working_dir : {working_dir}")
    print(f"  redis       : {rk['host']}:{rk['port']} db{rk['db']}{tls}{auth}")
    print(f"  claude tools: {tools}")
    print(f"  rate limit  : {args.rate_per_min}/min per conversation · Horizon caps every conversation")
    print(f"  watching for messages… (Ctrl+C to stop)\n")

    # The resident loop (spec §5.2/§5.4): heartbeat, recover crashed work, flush
    # offline/paused holds, then poll. Wrapped with reconnect/backoff so a remote
    # bus drop (TLS/network) is survived, not fatal; bad auth IS fatal.
    obs.recover_pending()
    obs.flush_hold()
    backoff = 1
    try:
        while True:
            try:
                heartbeat.beat(args.project_id)
                obs.flush_hold()
                obs.flush_paused()
                n = obs.poll_once(block_ms=args.block_ms)
                if n:
                    print(f"   [{args.project_id}] processed {n} message(s)")
                backoff = 1  # a healthy cycle resets the backoff
            except redis_lib.exceptions.AuthenticationError as e:
                sys.exit(f"\nRedis auth failed mid-run: {e}")
            except (redis_lib.exceptions.ConnectionError, redis_lib.exceptions.TimeoutError) as e:
                print(f"   [{args.project_id}] Redis connection lost ({e}); retry in {backoff}s…")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                try:
                    redis.ping()                # re-establish the connection
                    obs.recover_pending()       # reclaim our pending (PEL) after reconnect
                    print(f"   [{args.project_id}] reconnected.")
                except Exception:
                    pass                        # still down → stay in backoff
    except KeyboardInterrupt:
        print(f"\n=== Observatory '{args.project_id}' offline ===")


if __name__ == "__main__":
    main()
