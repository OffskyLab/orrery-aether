"""Cross-machine deployment tests (spec 2026-06-01-cross-machine).

Foundation layer: make_redis auth/TLS params (backward-compatible), the
connection resolver precedence, and Registry additive/duplicate-fail-closed.
CLI/observatory-level tests live in test_cli.py / are added alongside those steps.
"""
import inspect

import pytest
from redis.exceptions import WatchError

import aether.core.aether_client as ac
from aether.core import conn
from aether.core.aether_client import make_redis
from aether.core.registry import Body, DuplicateBodyError, Registry


# ---- F1: make_redis backward-compat + ssl passthrough ----------------------
def test_make_redis_no_args_byte_identical(monkeypatch):
    captured = {}

    class FakeRedis:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(ac.redis_lib, "Redis", FakeRedis)
    ac.make_redis()
    assert captured == {"host": "localhost", "port": 6379, "db": 0, "decode_responses": True}
    assert "ssl" not in captured and "password" not in captured and "username" not in captured


def test_make_redis_ssl_passthrough(monkeypatch):
    captured = {}

    class FakeRedis:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(ac.redis_lib, "Redis", FakeRedis)
    ac.make_redis(host="h", port=6380, ssl=True, ssl_ca_certs="/ca.pem",
                  password="pw", username="u")
    assert captured["ssl"] is True and captured["ssl_ca_certs"] == "/ca.pem"
    assert captured["password"] == "pw" and captured["username"] == "u"


def test_make_redis_signature_has_new_params():
    s = inspect.signature(make_redis)
    for p in ("password", "username", "ssl", "ssl_ca_certs", "ssl_certfile", "ssl_keyfile"):
        assert p in s.parameters
    assert s.parameters["password"].default is None
    assert s.parameters["ssl"].default is False


# ---- conn resolver precedence ----------------------------------------------
def test_resolve_precedence_flag_env_profile_default():
    k = conn.resolve_redis_kwargs(cli={"host": "clihost"},
                                  env={"AETHER_REDIS_HOST": "envhost"},
                                  profile={"host": "profhost"})
    assert k["host"] == "clihost"                       # flag wins
    k = conn.resolve_redis_kwargs(cli={"host": None},
                                  env={"AETHER_REDIS_HOST": "envhost"},
                                  profile={"host": "profhost"})
    assert k["host"] == "envhost"                       # env beats profile
    k = conn.resolve_redis_kwargs(cli=None, env={}, profile={"host": "profhost"})
    assert k["host"] == "profhost"                      # profile beats default
    k = conn.resolve_redis_kwargs(cli=None, env={}, profile={})
    assert k["host"] == "localhost" and k["port"] == 6379 and k["db"] == 0


def test_password_never_from_profile():
    k = conn.resolve_redis_kwargs(cli=None, env={}, profile={"password": "fromprofile"})
    assert "password" not in k
    k = conn.resolve_redis_kwargs(cli=None, env={"AETHER_REDIS_PASSWORD": "fromenv"}, profile={})
    assert k["password"] == "fromenv"


def test_resolve_profile_autoloaded(monkeypatch):
    monkeypatch.setattr(conn, "load_bus_profile", lambda *a, **k: {"host": "autoloaded"})
    k = conn.resolve_redis_kwargs(cli=None, env={})     # profile omitted → AUTO load
    assert k["host"] == "autoloaded"


def test_resolve_ssl_tristate():
    k = conn.resolve_redis_kwargs(cli={"ssl": False}, env={"AETHER_REDIS_TLS": "true"}, profile={})
    assert "ssl" not in k                               # cli False forces off, overriding env
    k = conn.resolve_redis_kwargs(cli={"ssl": True}, env={}, profile={})
    assert k["ssl"] is True
    k = conn.resolve_redis_kwargs(cli={"ssl": None}, env={"AETHER_REDIS_TLS": "1"}, profile={})
    assert k["ssl"] is True


def test_load_bus_profile_missing_or_bad_returns_empty(tmp_path):
    assert conn.load_bus_profile(str(tmp_path / "nope.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert conn.load_bus_profile(str(bad)) == {}


# ---- registry additive + duplicate fail-closed -----------------------------
def _body(pid, wd="/x"):
    return Body(project_id=pid, description="d", capabilities=[],
                inbox=f"aether:inbox:{pid}", working_dir=wd)


def test_sync_additive_does_not_delete_others(r):
    reg = Registry(r)
    reg.register_body(_body("alpha"))
    reg.sync({"beta": _body("beta")})                   # additive (default)
    assert reg.has("alpha") and reg.has("beta")


def test_sync_prune_deletes_missing_bodies(r):
    reg = Registry(r)
    reg.register_body(_body("alpha"))
    reg.sync({"beta": _body("beta")}, prune=True)
    assert not reg.has("alpha") and reg.has("beta")


def test_register_body_duplicate_fail_closed(r):
    reg = Registry(r)
    assert reg.register_body(_body("g", wd="/a")) == "added"
    assert reg.register_body(_body("g", wd="/a")) == "unchanged"      # idempotent
    with pytest.raises(DuplicateBodyError):
        reg.register_body(_body("g", wd="/b"))                        # conflicting → fail-closed
    assert reg.register_body(_body("g", wd="/b"), force=True) == "forced"
    assert reg.get("g").working_dir == "/b"


def test_register_body_cas_retries_on_watcherror():
    """A WatchError (key changed mid-CAS) must retry, not crash."""
    calls = {"execute": 0}

    class FakePipe:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def watch(self, key):
            pass

        def hget(self, key, field):
            return None                      # always "absent"

        def unwatch(self):
            pass

        def multi(self):
            pass

        def hset(self, *a):
            pass

        def execute(self):
            calls["execute"] += 1
            if calls["execute"] == 1:
                raise WatchError()           # first attempt loses the race

    class FakeRedis:
        def pipeline(self):
            return FakePipe()

    reg = Registry(FakeRedis())
    assert reg.register_body(_body("z")) == "added"
    assert calls["execute"] == 2             # retried once after WatchError
