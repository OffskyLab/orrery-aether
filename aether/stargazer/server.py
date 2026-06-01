"""Stargazer server — FastAPI + SSE, strictly read-only (spec §15.2, §16).

Built with a :class:`ReadOnlyRedis`, so the process has no reachable write path
(§16.1-6). Exposes GET-only endpoints and an SSE stream that sends a BOUNDED
recent backlog on connect and then tails live; a reconnect carrying
``Last-Event-ID`` resumes strictly after that id, so nothing is re-sent
(§16.1-7). Binds localhost only by default (§15.6 decision 3) — it shows every
project's message content, so it must not be exposed.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..core.registry import REGISTRY_KEY
from .events import EventReader
from .readonly import ReadOnlyRedis
from .viewmodels import (build_constellation, build_extinction_log,
                         build_operator_log, build_telescope, build_timeline)

HEARTBEAT_PREFIX = "aether:heartbeat"
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


def _hop_to_dict(h) -> dict:
    d = asdict(h)
    d["from"] = d.pop("from_")
    return d


def _online_map(ro) -> dict:
    reg = ro.hgetall(REGISTRY_KEY) or {}
    return {pid: ro.exists(f"{HEARTBEAT_PREFIX}:{pid}") == 1 for pid in reg.keys()}


async def sse_source(reader: EventReader, last_event_id: Optional[str],
                     recent_window: int = 200, block_ms: int = 500,
                     max_idle_polls: Optional[int] = None):
    """The SSE generator (standalone so it is unit-testable).

    Fresh connect → bounded recent backlog, then tail from its tip. Reconnect
    (``last_event_id`` set) → resume strictly after it, no backlog, no dup."""
    if last_event_id:
        cursor = last_event_id
    else:
        backlog = await asyncio.to_thread(reader.recent, recent_window)
        for eid, rec in backlog:
            yield {"id": eid, "event": rec.get("event_type", "message"),
                   "data": json.dumps(rec, ensure_ascii=False)}
        # When the backlog is empty, tail from "0" (everything after the start),
        # NOT "$": "$" is resolved by Redis at XREAD time, so an event landing in
        # the gap between recent() and XREAD would be skipped (a lost event,
        # violating §16.1-7 "no gap"). Since we sent no backlog, "0" can't dup.
        cursor = backlog[-1][0] if backlog else "0"

    idle = 0
    while True:
        batch = await asyncio.to_thread(reader.tail, cursor, 200, block_ms)
        if not batch:
            idle += 1
            if max_idle_polls is not None and idle >= max_idle_polls:
                return
            continue
        idle = 0
        for eid, rec in batch:
            cursor = eid
            yield {"id": eid, "event": rec.get("event_type", "message"),
                   "data": json.dumps(rec, ensure_ascii=False)}


def create_app(ro_redis, *, recent_window: int = 200, web_dir: str = WEB_DIR) -> FastAPI:
    """Build the Stargazer app over a read-only Redis facade.

    Raises if handed anything other than a ReadOnlyRedis — the read-only
    guarantee is a construction-time invariant, not a convention."""
    if not isinstance(ro_redis, ReadOnlyRedis):
        raise TypeError("Stargazer must be constructed with a ReadOnlyRedis (read-only invariant)")

    app = FastAPI(title="Stargazer", description="Aether read-only observatory")
    reader = EventReader(ro_redis)
    app.state.ro_redis = ro_redis        # introspected by the read-only test
    app.state.reader = reader
    app.state.recent_window = recent_window

    @app.get("/")
    def index():
        path = os.path.join(web_dir, "index.html")
        if os.path.exists(path):
            return FileResponse(path)
        return HTMLResponse("<h1>Stargazer</h1><p>web/index.html not found</p>")

    @app.get("/api/health")
    def health():
        return {"ok": True, "readonly": True}

    @app.get("/api/recent")
    def recent(window: int = 0):
        w = window or recent_window
        return JSONResponse([{"id": eid, "record": rec} for eid, rec in reader.recent(w)])

    @app.get("/api/timeline")
    def timeline(conversation_id: Optional[str] = None, window: int = 0):
        tl = build_timeline([rec for _id, rec in reader.recent(window or recent_window)],
                            conversation_id=conversation_id)
        return {
            "conversation_id": tl.conversation_id,
            "hops": [_hop_to_dict(h) for h in tl.hops],
            "summaries": tl.summaries,
            "terminal": tl.terminal,
            "actions": tl.actions,
        }

    @app.get("/api/operator_log")
    def operator_log(window: int = 0):
        records = [rec for _id, rec in reader.recent(window or recent_window)]
        return [asdict(a) for a in build_operator_log(records)]

    @app.get("/api/constellation")
    def constellation(window: int = 0):
        records = [rec for _id, rec in reader.recent(window or recent_window)]
        stars = build_constellation(records, _online_map(ro_redis))
        return {pid: asdict(s) for pid, s in stars.items()}

    @app.get("/api/extinction")
    def extinction(window: int = 0):
        records = [rec for _id, rec in reader.recent(window or recent_window)]
        return [
            {**asdict(e), "from": e.from_} for e in build_extinction_log(records)
        ]

    @app.get("/api/telescope")
    def telescope(conversation_id: str, window: int = 0):
        records = [rec for _id, rec in reader.recent(window or recent_window)]
        t = build_telescope(records, conversation_id)
        return {"conversation_id": t.conversation_id, "milestones": t.milestones,
                "ended": t.ended}

    @app.get("/stream")
    async def stream(request: Request, max_idle_polls: Optional[int] = None,
                     block_ms: int = 500):
        # ``max_idle_polls`` lets a smoke test get the backlog + a couple of tail
        # polls and then terminate; in production it is omitted → tail forever.
        last_event_id = request.headers.get("last-event-id")
        return EventSourceResponse(sse_source(
            reader, last_event_id, recent_window=recent_window,
            block_ms=block_ms, max_idle_polls=max_idle_polls))

    return app


def run(host=None, port=None, db=None,
        redis_host=None, redis_port=None):  # pragma: no cover
    """Launch Stargazer. Binds localhost only by default (spec §15.6 decision 3).

    Every parameter falls back to an ``AETHER_*`` env var, then to the original
    hard-coded default — so calling ``run()`` with no args and no env is
    identical to before. Inside a container set ``AETHER_STARGAZER_HOST=0.0.0.0``
    and ``AETHER_REDIS_HOST=redis``, but publish the host port on 127.0.0.1 only
    so the localhost-only EXPOSURE invariant still holds at the host boundary."""
    import redis as redis_lib
    import uvicorn

    host = host or os.environ.get("AETHER_STARGAZER_HOST", "127.0.0.1")
    port = int(port if port is not None else os.environ.get("AETHER_STARGAZER_PORT", 8765))
    db = int(db if db is not None else os.environ.get("AETHER_REDIS_DB", 0))
    redis_host = redis_host or os.environ.get("AETHER_REDIS_HOST", "localhost")
    redis_port = int(redis_port if redis_port is not None else os.environ.get("AETHER_REDIS_PORT", 6379))

    raw = redis_lib.Redis(host=redis_host, port=redis_port, db=db, decode_responses=True)
    app = create_app(ReadOnlyRedis(raw))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    run()
