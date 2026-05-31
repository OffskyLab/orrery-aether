"""Phase 3 · Scenario 4 — 離線星體 (spec §16.1-4).

A star with an expired heartbeat shows offline/dim; restoring the heartbeat makes
it active again. Tested at the view-model level and through the real heartbeat →
online-map wiring (read via ReadOnlyRedis).
"""
from aether.core.clock import SystemClock
from aether.core.heartbeat import Heartbeat
from aether.core.registry import Body, Registry
from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.server import _online_map
from aether.stargazer.viewmodels import build_constellation
from .p3_fixtures import msg


def test_star_offline_then_restored_view_model():
    records = [msg("c", "alpha", "beta", 0), msg("c", "beta", "alpha", 1)]
    off = build_constellation(records, online={"alpha": False, "beta": True})
    assert off["alpha"].online is False and off["beta"].online is True
    assert off["alpha"].activity > 0  # brightness reflects activity

    on = build_constellation(records, online={"alpha": True, "beta": True})
    assert on["alpha"].online is True


def test_online_map_reads_heartbeat_via_readonly(r):
    Registry(r).sync({
        "alpha": Body("alpha", "frontend", [], "aether:inbox:alpha"),
        "beta": Body("beta", "backend", [], "aether:inbox:beta"),
    })
    hb = Heartbeat(redis=r, clock=SystemClock())
    hb.beat("alpha")  # only alpha is alive

    om = _online_map(ReadOnlyRedis(r))
    assert om == {"alpha": True, "beta": False}

    hb.beat("beta")
    assert _online_map(ReadOnlyRedis(r)) == {"alpha": True, "beta": True}

    hb.go_offline("alpha")
    assert _online_map(ReadOnlyRedis(r)) == {"alpha": False, "beta": True}
