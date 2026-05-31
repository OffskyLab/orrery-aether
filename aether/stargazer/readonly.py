"""ReadOnlyRedis — a write-blocking facade over a Redis client (spec §16.1-6).

Stargazer is constructed with ONLY this facade. Every Redis *write* command
(xadd, set, hset, rpush, lpop, delete, xack, xgroup_create, expire, xtrim,
flushdb, …) is unreachable — attribute access for anything outside the read
allowlist raises ``AttributeError``. There is therefore no code path by which the
dashboard could publish a message, ACK an entry, or mutate the registry. The
structural test enumerates the write commands and asserts each one is blocked.
"""
from __future__ import annotations

# The complete set of commands Stargazer is permitted to use. Everything here is
# strictly read-only. If a future view needs another command, it must be a read
# and must be added here deliberately (a reviewable choke point).
READ_COMMANDS = frozenset({
    # streams
    "xrange", "xrevrange", "xread", "xlen", "xinfo_stream", "xinfo_groups", "xpending",
    # hashes (registry, session map — read only)
    "hget", "hgetall", "hexists", "hkeys", "hvals", "hlen",
    # generic / keys
    "exists", "get", "mget", "keys", "scan", "ttl", "pttl", "type", "strlen",
    # lists (hold queue inspection — read only)
    "llen", "lrange",
    # sets
    "smembers", "sismember", "scard",
    # health
    "ping",
})


class ReadOnlyRedis:
    """Wraps a Redis client, exposing read commands only."""

    def __init__(self, redis: object) -> None:
        # Bypass our own __setattr__ guard to stash the wrapped client.
        object.__setattr__(self, "_redis", redis)

    def __getattr__(self, name: str):
        if name in READ_COMMANDS:
            return getattr(object.__getattribute__(self, "_redis"), name)
        raise AttributeError(
            f"ReadOnlyRedis blocks '{name}': Stargazer is read-only and may only "
            f"use {sorted(READ_COMMANDS)!r}"
        )

    def __setattr__(self, key: str, value) -> None:
        raise AttributeError("ReadOnlyRedis is immutable (no write attributes)")

    def __delattr__(self, key: str) -> None:
        raise AttributeError("ReadOnlyRedis is immutable")
