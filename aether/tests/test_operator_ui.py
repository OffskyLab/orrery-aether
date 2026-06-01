"""Operator UI + unregister tests (spec 2026-06-01-operator-ui).

Covers the new write op (unregister: remove/online-guard/absent/audit), the
token-gated read endpoints the SPA needs, the SPA being served, and the shared
online_map helper. The Stargazer read-only isolation gate stays in
test_p4_operator.py (must remain green; not touched here).
"""
import pytest
from fastapi.testclient import TestClient

from aether.core.aether_client import AetherClient
from aether.core.envelope import new_envelope
from aether.core.heartbeat import Heartbeat
from aether.core.registry import Body, Registry, online_map
from aether.operator_panel.server import create_operator_app
from aether.stargazer.readonly import ReadOnlyRedis

TOK = "test-operator-token"
AUTH = {"Authorization": "Bearer " + TOK}


def _client(r):
    return TestClient(create_operator_app(r, TOK))


def _body(pid, wd="/tmp"):
    return Body(project_id=pid, description=f"d-{pid}", capabilities=["x"],
                inbox=f"aether:inbox:{pid}", working_dir=wd)


# ---- auth gates (no token → 401) -------------------------------------------
def test_unregister_requires_token(r):
    assert _client(r).post("/unregister", json={"project_id": "x"}).status_code == 401


def test_api_bodies_requires_token(r):
    assert _client(r).get("/api/bodies").status_code == 401


def test_api_conversations_requires_token(r):
    assert _client(r).get("/api/conversations").status_code == 401


# ---- unregister ------------------------------------------------------------
def test_unregister_removes_and_audits(r):
    Registry(r).add(_body("victim"))                       # offline (no heartbeat)
    res = _client(r).post("/unregister", json={"project_id": "victim"}, headers=AUTH)
    assert res.status_code == 200 and res.json()["state"] == "removed"
    assert not Registry(r).has("victim")
    evs = AetherClient(r).read_events()
    assert any(e.get("event_type") == "operator_action" and e.get("action") == "unregister"
               and e.get("project_id") == "victim" for e in evs)


def test_unregister_absent_body(r):
    res = _client(r).post("/unregister", json={"project_id": "ghost"}, headers=AUTH)
    assert res.status_code == 200
    assert res.json() == {"project_id": "ghost", "state": "absent", "removed": False}
    # absent path emits NO audit
    assert not any(e.get("action") == "unregister" for e in AetherClient(r).read_events())


def test_unregister_online_body_refused_without_force(r):
    Registry(r).add(_body("live"))
    Heartbeat(r).beat("live")                              # now online
    c = _client(r)
    assert c.post("/unregister", json={"project_id": "live"}, headers=AUTH).status_code == 409
    assert Registry(r).has("live")                         # not removed
    res = c.post("/unregister", json={"project_id": "live", "force": True}, headers=AUTH)
    assert res.status_code == 200 and res.json()["state"] == "removed"
    assert not Registry(r).has("live")
    assert not Heartbeat(r).is_online("live")              # heartbeat key deleted too


# ---- read endpoints --------------------------------------------------------
def test_api_bodies_shape(r):
    Registry(r).add(_body("alpha")); Registry(r).add(_body("beta"))
    Heartbeat(r).beat("alpha")                             # alpha online, beta not
    rows = _client(r).get("/api/bodies", headers=AUTH).json()
    by = {row["id"]: row for row in rows}
    assert by["alpha"]["online"] is True and by["beta"]["online"] is False
    assert by["alpha"]["description"] == "d-alpha" and by["alpha"]["working_dir"] == "/tmp"


def test_api_conversations_shape(r):
    c = _client(r)
    assert c.get("/api/conversations", headers=AUTH).json() == []   # none yet
    ac = AetherClient(r)
    ac.emit(new_envelope(from_="a", to="b", intent="ask", text="hi", conversation_id="t1"))
    ac.emit_event("terminated", conversation_id="t1", reason="operator_kill")
    ac.emit(new_envelope(from_="b", to="c", intent="inform", text="yo", conversation_id="t2"))
    rows = c.get("/api/conversations", headers=AUTH).json()
    cids = [row["conversation_id"] for row in rows]
    assert cids[0] == "t2"                                  # most recent first
    assert set(cids) == {"t1", "t2"}
    byc = {row["conversation_id"]: row for row in rows}
    assert byc["t1"]["from"] == "a" and byc["t1"]["to"] == "b" and byc["t1"]["ended"] is True
    assert byc["t2"]["from"] == "b" and byc["t2"]["to"] == "c" and byc["t2"]["ended"] is False


# ---- SPA + helper ----------------------------------------------------------
def test_get_root_serves_spa(r):
    res = _client(r).get("/")                               # no auth needed for the page
    assert res.status_code == 200 and "Aether Operator" in res.text


def test_online_map(r):
    Registry(r).add(_body("on")); Registry(r).add(_body("off"))
    Heartbeat(r).beat("on")
    expected = {"on": True, "off": False}
    assert online_map(r) == expected
    assert online_map(ReadOnlyRedis(r)) == expected         # read-only compatible
