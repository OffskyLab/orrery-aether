"""Phase 3 · smoke test (spec §16.2): page loads and the SSE endpoint connects.

A thin end-to-end check through the real FastAPI app + ReadOnlyRedis — distinct
from the view-model unit tests.
"""
from fastapi.testclient import TestClient

from aether.stargazer.readonly import ReadOnlyRedis
from aether.stargazer.server import create_app
from .p3_fixtures import msg, seed


def test_page_and_read_endpoints_load(r):
    seed(r, [msg("c", "alpha", "beta", 0)])
    client = TestClient(create_app(ReadOnlyRedis(r)))

    page = client.get("/")
    assert page.status_code == 200
    assert "STARGAZER" in page.text  # the SPA shell rendered

    assert client.get("/api/health").json() == {"ok": True, "readonly": True}
    assert len(client.get("/api/recent").json()) >= 1
    assert client.get("/api/timeline", params={"conversation_id": "c"}).json()["hops"]


def test_sse_endpoint_streams_event_stream(r):
    seed(r, [msg("c", "alpha", "beta", 0)])
    client = TestClient(create_app(ReadOnlyRedis(r)))

    # max_idle_polls makes the stream terminate after backlog + a short idle poll.
    resp = client.get("/stream", params={"max_idle_polls": 1, "block_ms": 20})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "event: message" in resp.text  # the backlog event was streamed
