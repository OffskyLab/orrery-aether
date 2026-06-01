"""Connection resolution for cross-machine Aether (spec 2026-06-01-cross-machine).

ONE place decides the Redis connection kwargs for every entry point, with a
strict precedence so behaviour is predictable across CLI flags, env vars, a
persisted local bus profile, and built-in defaults:

    flag (cli, non-None)  >  env (AETHER_REDIS_*)  >  profile (~/.aether)  >  default

Rationale (from the discussion):
- profile must NOT override env — env is how docker-compose injects
  ``AETHER_REDIS_HOST=redis`` into the web containers; a stale profile must not
  break that.
- the password is NEVER read from the profile (avoid a plaintext secret sitting
  in ~/.aether); it comes only from a flag or AETHER_REDIS_PASSWORD.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

DEFAULT_PROFILE_PATH = os.path.expanduser("~/.aether/config.json")

# Sentinel so callers can pass ``profile=None`` to mean "an empty profile" and
# omit it entirely to mean "auto-load from disk". Runtime call sites omit it.
_AUTO = "AUTO"

_DEFAULTS = {"host": "localhost", "port": 6379, "db": 0}
_TRUEY = {"1", "true", "yes", "on"}


def add_redis_cli_opts(ap) -> None:
    """Attach the standard --redis-* connection flags (None defaults; precedence
    is applied later by resolve_redis_kwargs). Shared by the entry-point scripts."""
    ap.add_argument("--redis-host", default=None)
    ap.add_argument("--redis-port", type=int, default=None)
    ap.add_argument("--redis-db", type=int, default=None)
    ap.add_argument("--redis-password", default=None,
                    help="prefer AETHER_REDIS_PASSWORD env (avoids shell history)")
    ap.add_argument("--redis-username", default=None)
    ap.add_argument("--redis-tls", dest="redis_tls", action="store_const", const=True, default=None)
    ap.add_argument("--redis-no-tls", dest="redis_tls", action="store_const", const=False)
    ap.add_argument("--redis-tls-ca", default=None)


def redis_cli_dict(args) -> dict:
    """Build the resolver cli dict from argparse Namespace (any missing → None)."""
    return {
        "host": getattr(args, "redis_host", None),
        "port": getattr(args, "redis_port", None),
        "db": getattr(args, "redis_db", None),
        "password": getattr(args, "redis_password", None),
        "username": getattr(args, "redis_username", None),
        "ssl": getattr(args, "redis_tls", None),
        "ssl_ca_certs": getattr(args, "redis_tls_ca", None),
    }


def load_bus_profile(path: str = DEFAULT_PROFILE_PATH) -> dict:
    """Read the persisted bus profile. Never raises — a missing/broken profile
    must never block a localhost run, it just contributes nothing."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}


def _env_bool(env: dict, key: str) -> Optional[bool]:
    v = env.get(key)
    if v is None:
        return None
    return v.strip().lower() in _TRUEY


def resolve_redis_kwargs(cli: Optional[dict] = None, *, env: Any = None,
                         profile: Any = _AUTO) -> dict:
    """Resolve make_redis() kwargs by precedence flag > env > profile > default.

    ``cli`` keys (any of host/port/db/password/username/ssl/ssl_ca_certs/
    ssl_certfile/ssl_keyfile) win when not None. ``profile`` defaults to the
    sentinel ``"AUTO"`` → auto-load ``~/.aether/config.json``; pass an explicit
    dict (or ``{}``) in tests to isolate. The password is taken from cli/env
    only (never profile)."""
    cli = cli or {}
    env = os.environ if env is None else env
    if profile == _AUTO:
        profile = load_bus_profile()
    profile = profile or {}

    def pick(key, env_key, *, allow_profile=True, default=None, cast=None):
        if cli.get(key) is not None:
            val = cli[key]
        elif env.get(env_key) is not None:
            val = env[env_key]
        elif allow_profile and profile.get(key) is not None:
            val = profile[key]
        else:
            val = default
        if val is not None and cast is not None:
            val = cast(val)
        return val

    out: dict = {
        "host": pick("host", "AETHER_REDIS_HOST", default=_DEFAULTS["host"]),
        "port": pick("port", "AETHER_REDIS_PORT", default=_DEFAULTS["port"], cast=int),
        "db": pick("db", "AETHER_REDIS_DB", default=_DEFAULTS["db"], cast=int),
    }

    # password: cli/env only, never profile.
    password = cli.get("password") if cli.get("password") is not None else env.get("AETHER_REDIS_PASSWORD")
    if password:
        out["password"] = password

    username = pick("username", "AETHER_REDIS_USERNAME")
    if username:
        out["username"] = username

    # ssl: tri-state cli (True/False/None) > env > profile.
    if cli.get("ssl") is not None:
        ssl = bool(cli["ssl"])
    else:
        env_ssl = _env_bool(env, "AETHER_REDIS_TLS")
        ssl = env_ssl if env_ssl is not None else bool(profile.get("ssl", False))
    if ssl:
        out["ssl"] = True
        ca = pick("ssl_ca_certs", "AETHER_REDIS_TLS_CA")
        if ca:
            out["ssl_ca_certs"] = ca
        cert = pick("ssl_certfile", "AETHER_REDIS_TLS_CERT")
        if cert:
            out["ssl_certfile"] = cert
        key = pick("ssl_keyfile", "AETHER_REDIS_TLS_KEY")
        if key:
            out["ssl_keyfile"] = key

    return out
