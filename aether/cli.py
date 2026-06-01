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
def _cli_dict(args) -> dict:
    """Collect the (possibly-None) connection flags into a resolver cli dict."""
    return {
        "host": getattr(args, "redis_host", None),
        "port": getattr(args, "redis_port", None),
        "db": getattr(args, "redis_db", None),
        "password": getattr(args, "redis_password", None),
        "username": getattr(args, "redis_username", None),
        "ssl": getattr(args, "redis_tls", None),
        "ssl_ca_certs": getattr(args, "redis_tls_ca", None),
    }

def _resolved_redis(args) -> dict:
    from aether.core.conn import resolve_redis_kwargs
    return resolve_redis_kwargs(cli=_cli_dict(args))

def _make_redis(args):
    from aether.core.aether_client import make_redis
    return make_redis(**_resolved_redis(args))

def _project_id(args):
    return sanitize_id(args.id) if getattr(args, "id", None) else sanitize_id(os.path.basename(os.getcwd()))

def _add_redis_opts(p):
    # Defaults are None (tri-state): resolve_redis_kwargs applies precedence
    # flag > env > profile > default, so a bare command behaves exactly as before.
    p.add_argument("--redis-host", default=None)
    p.add_argument("--redis-port", type=int, default=None)
    p.add_argument("--redis-db", type=int, default=None)
    p.add_argument("--redis-password", default=None,
                   help="prefer AETHER_REDIS_PASSWORD env over the flag (avoids shell history)")
    p.add_argument("--redis-username", default=None)
    p.add_argument("--redis-tls", dest="redis_tls", action="store_const", const=True, default=None,
                   help="connect over TLS")
    p.add_argument("--redis-no-tls", dest="redis_tls", action="store_const", const=False,
                   help="force TLS off (overrides env/profile)")
    p.add_argument("--redis-tls-ca", default=None, help="CA cert (PEM) for TLS verification")


def _add_bus_opts(p):
    # Endpoint-style flags for `bus use` / `register`: here --host/--port name the
    # BUS (Redis) endpoint — matches `aether register --host 1.2.3.4 --port 6380`.
    # Same dests as _add_redis_opts so _cli_dict / resolver pick them up.
    p.add_argument("--host", dest="redis_host", default=None, help="bus (Redis) host")
    p.add_argument("--port", dest="redis_port", type=int, default=None, help="bus (Redis) port")
    p.add_argument("--db", dest="redis_db", type=int, default=None)
    p.add_argument("--password", dest="redis_password", default=None,
                   help="prefer AETHER_REDIS_PASSWORD env (avoids shell history)")
    p.add_argument("--username", dest="redis_username", default=None)
    p.add_argument("--tls", dest="redis_tls", action="store_const", const=True, default=None,
                   help="connect over TLS")
    p.add_argument("--no-tls", dest="redis_tls", action="store_const", const=False,
                   help="force TLS off (overrides env/profile)")
    p.add_argument("--tls-ca", dest="redis_tls_ca", default=None, help="CA cert (PEM) for TLS")


# --- handlers ---------------------------------------------------------------
def cmd_mcp_setup(args) -> int:
    project = _project_id(args)
    identity = args.identity or f"{project}-mcp"     # stable, never random (C4)
    # Resolve BEFORE writing — args.redis_* default to None now; passing them raw
    # would write "None" into .mcp.json env (audit H5). Resolver gives real values.
    kw = _resolved_redis(args)
    r_db, r_host, r_port = kw["db"], kw["host"], kw["port"]
    entry = build_mcp_server_entry(
        sys.executable, MCP_SERVER_SCRIPT, identity,           # absolute python (C cross-cutting a)
        redis_db=r_db, constellation_abs=CONSTELLATION_PATH,
        redis_host=(r_host if r_host != "localhost" else None),
        redis_port=(r_port if r_port != 6379 else None))

    if args.method == "claude-cli":
        import shutil, subprocess
        if shutil.which("claude"):
            cmd = ["claude", "mcp", "add", "aether", "--scope", args.scope,
                   "-e", f"AETHER_REDIS_DB={r_db}",
                   "-e", f"AETHER_CONSTELLATION={CONSTELLATION_PATH}"]
            if r_host != "localhost":
                cmd += ["-e", f"AETHER_REDIS_HOST={r_host}"]
            if r_port != 6379:
                cmd += ["-e", f"AETHER_REDIS_PORT={r_port}"]
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

    # connect to the "server" (Redis) and register ONLY this body (additive,
    # fail-closed) — not the whole constellation (which would re-register peers).
    from aether.core.registry import Body, DuplicateBodyError, Registry
    try:
        r = _make_redis(args)
        r.ping()
    except Exception as e:
        print(f"⚠ Redis not reachable ({e}). Start it:", file=sys.stderr)
        print(f"    docker compose -f {COMPOSE_FILE} up -d redis", file=sys.stderr)
        return 1
    body = Body(project_id=project, description=description, capabilities=capabilities,
                inbox=inbox_stream(project), working_dir=cwd)
    try:
        Registry(r).register_body(body, force=getattr(args, "force", False))
    except DuplicateBodyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("✓ connected to Redis and registered this body")
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


def cmd_bus_use(args) -> int:
    """Point this machine at a (remote) bus and PERSIST the endpoint to a local
    gitignored profile, so observatory/send/who/mcp inherit it without re-typing.
    Pings with the resolved connection (incl. password from env) BEFORE writing,
    so a half-complete unreachable profile never poisons later commands. The
    password is NEVER written to the profile (env-only)."""
    from aether.core.aether_client import make_redis
    from aether.core.conn import DEFAULT_PROFILE_PATH, resolve_redis_kwargs
    kw = resolve_redis_kwargs(cli=_cli_dict(args))
    try:
        make_redis(**kw).ping()
    except Exception as e:
        print(f"ERROR: cannot reach bus at {kw['host']}:{kw['port']} ({e}); profile NOT written.",
              file=sys.stderr)
        return 1
    profile = {"name": "default", "host": kw["host"], "port": kw["port"], "db": kw["db"]}
    if kw.get("ssl"):
        profile["ssl"] = True
    if kw.get("ssl_ca_certs"):
        profile["ssl_ca_certs"] = kw["ssl_ca_certs"]
    if kw.get("username"):
        profile["username"] = kw["username"]
    os.makedirs(os.path.dirname(DEFAULT_PROFILE_PATH), exist_ok=True)
    with open(DEFAULT_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    os.chmod(DEFAULT_PROFILE_PATH, 0o600)
    print(f"✓ bus profile saved → {DEFAULT_PROFILE_PATH} "
          f"({kw['host']}:{kw['port']} db{kw['db']}{' TLS' if kw.get('ssl') else ''})")
    if kw.get("password"):
        print("  note: password came from AETHER_REDIS_PASSWORD and was NOT stored in the profile.")
    return 0


def cmd_register(args) -> int:
    """Convenience: join a (remote) bus + register this project's body. = `bus use`
    (when --host given, atomically ping→persist) followed by `client setup`."""
    if getattr(args, "redis_host", None):
        rc = cmd_bus_use(args)
        if rc != 0:
            return rc
    return cmd_client_setup(args)


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
    cs.add_argument("--force", action="store_true", help="overwrite an existing body with the same id")
    _add_redis_opts(cs); cs.set_defaults(func=cmd_client_setup)

    # bus use (persist a remote bus endpoint to ~/.aether/config.json)
    bus = sub.add_parser("bus", help="bus endpoint profile").add_subparsers(dest="sub", required=True)
    bu = bus.add_parser("use", help="point this machine at a (remote) bus + persist the endpoint")
    _add_bus_opts(bu); bu.set_defaults(func=cmd_bus_use)

    # register (= bus use + client setup) — uses endpoint-style --host/--port
    reg = sub.add_parser("register", help="join a (remote) bus + register this project's body")
    reg.add_argument("--id"); reg.add_argument("--description")
    reg.add_argument("--capability", action="append", help="repeatable")
    reg.add_argument("--yes", action="store_true", help="non-interactive")
    reg.add_argument("--force", action="store_true", help="overwrite an existing body with the same id")
    _add_bus_opts(reg); reg.set_defaults(func=cmd_register)

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
