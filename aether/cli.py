"""The unified `aether` CLI (spec 2026-05-31-aether-cli).

A thin argparse dispatcher that unifies the existing Aether scripts and adds the
two setup commands. It does NOT reimplement logic — alias subcommands forward to
each script's ``main(argv)``.

Runnable three ways (no packaging required, spec C1):
  python3 -m aether.cli <cmd>
  python3 /abs/path/aether/cli.py <cmd>        # from any cwd
  aether <cmd>                                 # after `aether install-shim`
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# --- import-root bootstrap (must precede any `aether.*` import) -------------
# Owned here at module level so subcommand handlers never re-insert (spec §1).
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))      # .../aether
_REPO_ROOT = os.path.dirname(_PKG_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aether.cli_support import (append_constellation_body, build_mcp_server_entry,  # noqa: E402
                                infer_metadata, merge_mcp_config, sanitize_id)

MCP_SERVER_SCRIPT = os.path.join(_PKG_DIR, "mcp_server.py")
CONSTELLATION_PATH = os.path.join(_PKG_DIR, "constellation.yaml")
COMPOSE_FILE = os.path.join(_PKG_DIR, "docker-compose.yml")


# --- shared helpers ---------------------------------------------------------
def _make_redis(args):
    from aether.core.aether_client import make_redis
    return make_redis(host=args.redis_host, port=args.redis_port, db=args.redis_db)

def _project_id(args):
    return sanitize_id(args.id) if getattr(args, "id", None) else sanitize_id(os.path.basename(os.getcwd()))

def _add_redis_opts(p):
    p.add_argument("--redis-host", default=os.environ.get("AETHER_REDIS_HOST", "localhost"))
    p.add_argument("--redis-port", type=int, default=int(os.environ.get("AETHER_REDIS_PORT", "6379")))
    p.add_argument("--redis-db", type=int, default=int(os.environ.get("AETHER_REDIS_DB", "0")))


# --- handlers ---------------------------------------------------------------
def cmd_mcp_setup(args) -> int:
    project = _project_id(args)
    identity = args.identity or f"{project}-mcp"     # stable, never random (C4)
    entry = build_mcp_server_entry(
        sys.executable, MCP_SERVER_SCRIPT, identity,           # absolute python (C cross-cutting a)
        redis_db=args.redis_db, constellation_abs=CONSTELLATION_PATH,
        redis_host=(args.redis_host if args.redis_host != "localhost" else None),
        redis_port=(args.redis_port if args.redis_port != 6379 else None))

    if args.method == "claude-cli":
        import shutil, subprocess
        if shutil.which("claude"):
            cmd = ["claude", "mcp", "add", "aether", "--scope", args.scope,
                   "-e", f"AETHER_REDIS_DB={args.redis_db}",
                   "-e", f"AETHER_CONSTELLATION={CONSTELLATION_PATH}"]
            if args.redis_host != "localhost":
                cmd += ["-e", f"AETHER_REDIS_HOST={args.redis_host}"]
            if args.redis_port != 6379:
                cmd += ["-e", f"AETHER_REDIS_PORT={args.redis_port}"]
            cmd += ["--", sys.executable, MCP_SERVER_SCRIPT, "--identity", identity]
            print("running:", " ".join(cmd))
            return subprocess.call(cmd)
        print("WARNING: `claude` not on PATH — falling back to .mcp.json merge.")

    # .mcp.json merge (default) — local scope writes a gitignored file name.
    fname = ".mcp.local.json" if args.scope == "local" else ".mcp.json"
    path = os.path.join(os.getcwd(), fname)
    existing = {}
    if os.path.isfile(path):
        try:
            existing = json.load(open(path, encoding="utf-8"))
        except (ValueError, OSError) as e:
            print(f"ERROR: {fname} is not valid JSON ({e}); not modifying it.", file=sys.stderr)
            return 1
    try:
        merged = merge_mcp_config(existing, "aether", entry)
    except ValueError as e:
        print(f"ERROR: {e}; not modifying {fname}.", file=sys.stderr)
        return 1
    json.dump(merged, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")

    print(f"✓ wrote {fname} (server 'aether', identity '{identity}', scope {args.scope})")
    if args.scope == "project":
        print("  NOTE: this file contains machine-specific ABSOLUTE paths and will be committed.")
        print("        Use `--scope local` (gitignored) if you don't want that in the repo.")
    print("  → Restart Claude Code or run `/mcp` to load it (project scope needs approval on first use).")
    print("  → Slash commands appear as /mcp__aether__<prompt> (who / ask / poll / discuss / transcript / stop).")
    print("  ⚠ The star chart may be empty until you run `aether client setup` or start an Observatory.")
    return 0


def cmd_client_setup(args) -> int:
    from aether.core.aether_client import inbox_stream
    project = _project_id(args)
    cwd = os.getcwd()
    suggested = infer_metadata(cwd)
    description = args.description if args.description is not None else suggested["description"]
    capabilities = args.capability if args.capability else suggested["capabilities"]

    interactive = sys.stdin.isatty() and not args.yes
    if interactive:
        d = input(f"description [{description}]: ").strip()
        if d:
            description = d
        c = input(f"capabilities (comma-sep) [{','.join(capabilities)}]: ").strip()
        if c:
            capabilities = [x.strip() for x in c.split(",") if x.strip()]
    else:
        print(f"  (non-interactive) description={description!r} capabilities={capabilities}")

    fields = {"description": description, "capabilities": capabilities,
              "inbox": inbox_stream(project), "working_dir": cwd}
    text = open(CONSTELLATION_PATH, encoding="utf-8").read() if os.path.isfile(CONSTELLATION_PATH) else "bodies:\n"
    new_text, action = append_constellation_body(text, project, fields)
    if action == "appended":
        open(CONSTELLATION_PATH, "w", encoding="utf-8").write(new_text)
        print(f"✓ registered body '{project}' (working_dir={cwd}) in constellation.yaml")
    else:
        print(f"· body '{project}' already in constellation.yaml (unchanged)")

    # connect to the "server" (Redis)
    try:
        r = _make_redis(args)
        r.ping()
        from aether.core.registry import Registry
        Registry(r).load_and_sync(CONSTELLATION_PATH)
        print("✓ connected to Redis and published the star chart")
    except Exception as e:
        print(f"⚠ Redis not reachable ({e}). Start it:", file=sys.stderr)
        print(f"    docker compose -f {COMPOSE_FILE} up -d redis", file=sys.stderr)
        return 1
    print(f"  → To go online (receive messages): aether observatory {project}")
    print("  → To let THIS session reach others in Claude Code: aether mcp setup")
    return 0


def cmd_server_status(args) -> int:
    try:
        r = _make_redis(args)
        r.ping()
    except Exception as e:
        print(f"Redis: DOWN ({e})")
        return 1
    print(f"Redis: UP ({args.redis_host}:{args.redis_port} db{args.redis_db})")
    from aether.core.registry import Registry
    from aether.core.heartbeat import Heartbeat
    from aether.core.clock import SystemClock
    hb = Heartbeat(r, clock=SystemClock())
    bodies = Registry(r).all()
    if not bodies:
        print("Bodies: (none registered — run `aether client setup`)")
    for pid in sorted(bodies):
        print(f"  {'●' if hb.is_online(pid) else '○'} {pid} {'online' if hb.is_online(pid) else 'offline'}")
    return 0


def cmd_who(args) -> int:
    return cmd_server_status(args)


def cmd_install_shim(args) -> int:
    target_dir = os.path.expanduser(args.dir)
    os.makedirs(target_dir, exist_ok=True)
    shim = os.path.join(target_dir, "aether")
    body = f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(_PKG_DIR, "cli.py")}" "$@"\n'
    open(shim, "w").write(body)
    os.chmod(shim, 0o755)
    print(f"✓ installed shim: {shim}")
    if target_dir not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"  ⚠ {target_dir} is not on your PATH — add it, e.g.:")
        print(f'    export PATH="{target_dir}:$PATH"')
    print("  Then: aether mcp setup / aether client setup / aether who")
    return 0


def _forward(module_name, rest) -> int:
    import importlib
    mod = importlib.import_module(module_name)
    return int(mod.main(rest) or 0)


# Commands that forward the rest of the command line VERBATIM to an existing
# script's main(). Dispatched in main() BEFORE argparse: argparse.REMAINDER
# mishandles a remainder that STARTS WITH AN OPTION (`aether send --to x` →
# "unrecognized arguments: --to"; CPython bpo-17050). Intercepting here is what
# makes leading options forward correctly. Keys are argv prefixes (longest match
# wins — `mcp serve` must beat a bare `mcp`).
_PASSTHROUGH = {
    ("observatory",): "aether.run_observatory",
    ("send",): "aether.send_message",
    ("consult",): "aether.consult",
    ("mcp", "serve"): "aether.mcp_server",
}


# --- parser -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aether", description="Aether — cross-project agent bus CLI.")
    sub = p.add_subparsers(dest="command", required=True)

    # mcp setup | mcp serve
    mcp = sub.add_parser("mcp", help="MCP server setup / serve").add_subparsers(dest="sub", required=True)
    ms = mcp.add_parser("setup", help="register the Aether MCP server in this project")
    ms.add_argument("--id"); ms.add_argument("--identity")
    ms.add_argument("--scope", choices=["project", "local", "user"], default="project")
    ms.add_argument("--method", choices=["mcp-json", "claude-cli"], default="mcp-json")
    _add_redis_opts(ms); ms.set_defaults(func=cmd_mcp_setup)
    mserve = mcp.add_parser("serve", help="run the MCP server (Claude Code launches this)")
    mserve.add_argument("rest", nargs=argparse.REMAINDER)
    mserve.set_defaults(func=lambda a: _forward("aether.mcp_server", a.rest))

    # client setup
    cl = sub.add_parser("client", help="register this project on the bus").add_subparsers(dest="sub", required=True)
    cs = cl.add_parser("setup", help="register this project as a Body + connect to Redis")
    cs.add_argument("--id"); cs.add_argument("--description")
    cs.add_argument("--capability", action="append", help="repeatable")
    cs.add_argument("--yes", action="store_true", help="non-interactive (use inferred/flag values)")
    _add_redis_opts(cs); cs.set_defaults(func=cmd_client_setup)

    # server status
    sv = sub.add_parser("server", help="bus (Redis) status").add_subparsers(dest="sub", required=True)
    st = sv.add_parser("status", help="is Redis up + who is online")
    _add_redis_opts(st); st.set_defaults(func=cmd_server_status)

    # who
    who = sub.add_parser("who", help="list bodies + online status")
    _add_redis_opts(who); who.set_defaults(func=cmd_who)

    # install-shim
    sh = sub.add_parser("install-shim", help="install a bare `aether` command on PATH")
    sh.add_argument("--dir", default="~/.local/bin")
    sh.set_defaults(func=cmd_install_shim)

    # aliases (single-token passthroughs) — listed here so `aether --help` shows
    # them and positional-only invocations still parse. Real dispatch happens in
    # main()'s pre-argparse passthrough (see _PASSTHROUGH), which also forwards
    # LEADING OPTIONS correctly. `mcp serve` is registered under `mcp` above.
    for prefix, mod in _PASSTHROUGH.items():
        if len(prefix) != 1:
            continue
        a = sub.add_parser(prefix[0], help=f"alias for {mod.split('.')[-1]}.py")
        a.add_argument("rest", nargs=argparse.REMAINDER)
        a.set_defaults(func=(lambda m: (lambda args: _forward(m, args.rest)))(mod))

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Passthrough commands forward verbatim — intercept before argparse so a
    # leading option (e.g. `send --to ...`) isn't eaten by the top-level parser.
    for prefix, mod in sorted(_PASSTHROUGH.items(), key=lambda kv: -len(kv[0])):
        if tuple(argv[:len(prefix)]) == prefix:
            return _forward(mod, argv[len(prefix):])
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
