"""Phase 3 · Scenario 6 — 唯讀不變量（關鍵安全）(spec §16.1-6).

Structural proof that Stargazer has NO write path to Redis / inbox / registry:
every Redis write command is unreachable through the ReadOnlyRedis facade, the
server refuses to be built with a writable client, and every HTTP route is
GET/HEAD only. The observer can never become an actor.
"""
import pytest

from aether.stargazer.readonly import READ_COMMANDS, ReadOnlyRedis
from aether.stargazer.server import create_app

# Every write/mutating Redis command we must be sure is unreachable.
WRITE_COMMANDS = [
    "xadd", "xtrim", "xack", "xgroup_create", "xgroup_destroy", "xreadgroup",
    "xclaim", "xautoclaim", "xdel",
    "set", "setex", "setnx", "getset", "append", "incr", "decr",
    "hset", "hsetnx", "hdel", "hincrby",
    "rpush", "lpush", "lpop", "rpop", "lset", "lrem",
    "sadd", "srem", "spop", "zadd", "zrem",
    "delete", "unlink", "rename", "expire", "pexpire", "persist",
    "flushdb", "flushall", "pipeline", "execute_command",
]


def test_readonly_facade_allows_reads(r):
    ro = ReadOnlyRedis(r)
    assert ro.ping() is True
    ro.xrange("aether:events")        # does not raise
    ro.hgetall("aether:registry")     # does not raise


def test_readonly_facade_blocks_every_write_command(r):
    ro = ReadOnlyRedis(r)
    for cmd in WRITE_COMMANDS:
        with pytest.raises(AttributeError):
            getattr(ro, cmd)
        assert cmd not in READ_COMMANDS  # and it's deliberately not allowlisted


def test_readonly_facade_is_immutable(r):
    ro = ReadOnlyRedis(r)
    with pytest.raises(AttributeError):
        ro.anything = 1
    with pytest.raises(AttributeError):
        del ro.ping


def test_server_must_be_built_with_readonly_redis(r):
    # Raw, writable client is rejected at construction.
    with pytest.raises(TypeError):
        create_app(r)
    # The accepted app holds only a ReadOnlyRedis.
    app = create_app(ReadOnlyRedis(r))
    assert isinstance(app.state.ro_redis, ReadOnlyRedis)


def test_no_route_accepts_write_methods(r):
    app = create_app(ReadOnlyRedis(r))
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        assert methods <= {"GET", "HEAD"}, (getattr(route, "path", route), methods)
