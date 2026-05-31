"""Pytest fixtures for the Aether acceptance tests (Phase 1 + Phase 2).

Tests run against a REAL Redis (a dedicated DB, flushed per test) — only the
``claude`` CLI and the wall clock are faked/injected (spec §11.2 / §14.2).
Real-claude e2e tests are marked ``e2e`` and skipped unless ``--run-e2e`` (or
AETHER_RUN_E2E=1) is given, so the default suite stays fast and CI-able.
"""
from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import redis as redis_lib  # noqa: E402

from aether.core.aether_client import AetherClient  # noqa: E402
from aether.core.clock import ManualClock  # noqa: E402
from aether.core.control import ControlPlane  # noqa: E402
from aether.core.guards import RateLimiter  # noqa: E402
from aether.core.heartbeat import Heartbeat  # noqa: E402
from aether.core.processing_log import ProcessingLog  # noqa: E402
from aether.core.registry import Body, Registry  # noqa: E402
from aether.core.session_store import SessionStore  # noqa: E402
from aether.observatory.claude_runner import FakeClaudeRunner  # noqa: E402
from aether.observatory.crash import CrashController  # noqa: E402
from aether.observatory.main import Observatory  # noqa: E402

REDIS_HOST = os.environ.get("AETHER_TEST_REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("AETHER_TEST_REDIS_PORT", "6379"))
REDIS_TEST_DB = int(os.environ.get("AETHER_TEST_REDIS_DB", "15"))


# ---- e2e gating ------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption("--run-e2e", action="store_true", default=False,
                     help="run real claude -p end-to-end tests (slow)")


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: real claude -p end-to-end test (slow, gated)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-e2e") or os.environ.get("AETHER_RUN_E2E"):
        return
    skip = pytest.mark.skip(reason="needs --run-e2e or AETHER_RUN_E2E=1")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


# ---- Redis -----------------------------------------------------------------
@pytest.fixture(scope="session")
def _redis_available():
    r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_TEST_DB,
                        decode_responses=True)
    try:
        r.ping()
    except redis_lib.exceptions.RedisError as e:
        pytest.fail(
            f"Redis not reachable at {REDIS_HOST}:{REDIS_PORT} db{REDIS_TEST_DB}: {e}\n"
            "Start it with:  docker compose -f aether/docker-compose.yml up -d redis",
            pytrace=False,
        )
    return r


@pytest.fixture
def r(_redis_available):
    client = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_TEST_DB,
                             decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()


@pytest.fixture
def client(r):
    return AetherClient(r)


@pytest.fixture
def clock():
    return ManualClock(start=1_000_000.0)


# ---- Phase 1 compatible factory (no registry/heartbeat → Phase 1 behavior) -
@pytest.fixture
def make_obs(r, client, clock):
    def _make(project_id, responder, *, max_per_window=10_000, window_seconds=60,
              subscribe_broadcast=False, consumer=None):
        runner = FakeClaudeRunner(responder)
        rate = RateLimiter(redis=r, max_per_window=max_per_window,
                           window_seconds=window_seconds, clock=clock)
        proclog = ProcessingLog(redis=r)
        obs = Observatory(project_id, client, runner, rate, proclog,
                          subscribe_broadcast=subscribe_broadcast, consumer=consumer)
        return obs

    return _make


# ---- Phase 2 factory (registry + heartbeat + session store + crash) --------
@pytest.fixture
def heartbeat(r, clock):
    return Heartbeat(redis=r, clock=clock)


@pytest.fixture
def registry(r):
    reg = Registry(r)
    reg.sync({
        "project_alpha": Body("project_alpha", "frontend & design", ["ui", "react"],
                              "aether:inbox:project_alpha"),
        "project_beta": Body("project_beta", "backend API & db", ["api", "db", "auth"],
                             "aether:inbox:project_beta"),
        "project_gamma": Body("project_gamma", "data & analytics", ["etl", "reports"],
                              "aether:inbox:project_gamma"),
        "project_delta": Body("project_delta", "ops & infra", ["deploy", "infra"],
                              "aether:inbox:project_delta"),
    })
    return reg


@pytest.fixture
def make_p2_obs(r, client, clock, registry, heartbeat):
    """Full Phase 2 Observatory: registry-routed, heartbeat-aware, persisted
    sessions, crash-injectable. Returns (observatory, crash_controller)."""

    def _make(project_id, responder, *, max_per_window=10_000, window_seconds=60,
              malformed_retries=1, online=True, consumer=None,
              empty_content_lint=True, register_policy=None,
              subscribe_broadcast=False, control_plane=None):
        if online:
            heartbeat.beat(project_id)
        runner = FakeClaudeRunner(responder)
        rate = RateLimiter(redis=r, max_per_window=max_per_window,
                           window_seconds=window_seconds, clock=clock)
        proclog = ProcessingLog(redis=r)
        crash = CrashController()
        obs = Observatory(
            project_id, client, runner, rate, proclog,
            session_store=SessionStore(r, project_id),
            registry=registry, heartbeat=heartbeat, crash_controller=crash,
            control_plane=control_plane,
            malformed_retries=malformed_retries, consumer=consumer,
            empty_content_lint=empty_content_lint, register_policy=register_policy,
            subscribe_broadcast=subscribe_broadcast,
        )
        return obs, crash

    return _make


@pytest.fixture
def control_plane(r):
    return ControlPlane(r)


@pytest.fixture
def operator_app(r):
    """The authenticated operator control-plane app, built over a WRITABLE Redis
    (it is the privileged write path) with a known token."""
    from aether.operator_panel.server import create_operator_app
    return create_operator_app(r, token="test-operator-token")
