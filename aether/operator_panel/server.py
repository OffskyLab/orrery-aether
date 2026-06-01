"""Operator control-plane HTTP service (spec §18.2 / §18.3-1).

A SEPARATE FastAPI app from Stargazer. Every write endpoint requires a bearer
token (localhost + token, §18.3 decision 1); an unauthenticated write is rejected
with 401. Built with a WRITABLE Redis — it is the privileged write path, by
design — which is exactly why it must never share a process with the read-only
Stargazer.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..core.aether_client import AetherClient
from ..core.control import ControlPlane
from ..core.registry import Registry, online_map
from .control_service import BodyOnlineError, OperatorService

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


class InjectBody(BaseModel):
    to: str
    intent: str = "inform"
    text: str
    conversation_id: Optional[str] = None
    solicit: bool = False
    max_hops: int = 8


class ConversationBody(BaseModel):
    conversation_id: str


class ProjectBody(BaseModel):
    project_id: str


class UnregisterBody(BaseModel):
    project_id: str
    force: bool = False


def create_operator_app(redis, token: str) -> FastAPI:
    """Build the operator control-plane app over a WRITABLE Redis + a token.

    ``redis`` must be a normal writable client (NOT ReadOnlyRedis): this service
    is the authorized write path. ``token`` is the bearer secret every write
    requires."""
    if not token:
        raise ValueError("operator panel requires a non-empty token (§18.3 localhost+token)")

    client = AetherClient(redis)
    control = ControlPlane(redis)
    service = OperatorService(client, control)

    # docs_url/redoc_url/openapi_url disabled: otherwise FastAPI serves the route
    # schema UNAUTHENTICATED — only GET / (the SPA) and GET /health stay open.
    app = FastAPI(title="Aether Operator", description="Authenticated control plane",
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.service = service

    def require_token(authorization: Optional[str] = Header(default=None)) -> None:
        # Accept "Bearer <token>" or the bare token. Missing/wrong → 401.
        supplied = authorization
        if supplied and supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        if not supplied or supplied != token:
            raise HTTPException(status_code=401, detail="missing or invalid operator token")

    @app.get("/health")  # liveness only — no auth, no state change
    def health():
        return {"ok": True, "service": "operator"}

    @app.post("/inject", dependencies=[Depends(require_token)])
    def inject(body: InjectBody):
        return service.inject(to=body.to, intent=body.intent, text=body.text,
                              conversation_id=body.conversation_id,
                              solicit=body.solicit, max_hops=body.max_hops)

    @app.post("/pause", dependencies=[Depends(require_token)])
    def pause(body: ConversationBody):
        return service.pause(body.conversation_id)

    @app.post("/resume", dependencies=[Depends(require_token)])
    def resume(body: ConversationBody):
        return service.resume(body.conversation_id)

    @app.post("/terminate", dependencies=[Depends(require_token)])
    def terminate(body: ConversationBody):
        return service.terminate(body.conversation_id)

    @app.post("/kill_project", dependencies=[Depends(require_token)])
    def kill_project(body: ProjectBody):
        return service.kill_project(body.project_id)

    @app.post("/unregister", dependencies=[Depends(require_token)])
    def unregister(body: UnregisterBody):
        try:
            return service.unregister(body.project_id, force=body.force)
        except BodyOnlineError as e:
            raise HTTPException(status_code=409, detail=str(e))

    # ---- reads the UI needs (token-gated; reading on the write service is fine —
    #      the read-only invariant is Stargazer's, not a global no-read rule) -----
    @app.get("/api/bodies", dependencies=[Depends(require_token)])
    def api_bodies():
        om = online_map(redis)
        return [
            {"id": pid, "online": om.get(pid, False), "description": body.description,
             "capabilities": body.capabilities, "working_dir": body.working_dir}
            for pid, body in Registry(redis).all().items()
        ]

    @app.get("/api/conversations", dependencies=[Depends(require_token)])
    def api_conversations():
        seen: dict = {}                                  # cid -> row; insertion order = newest thread first
        for rec in reversed(client.read_events()):       # read_events is oldest→newest
            cid = rec.get("conversation_id")
            if not cid:
                continue
            row = seen.setdefault(cid, {"conversation_id": cid, "from": None,
                                        "to": None, "ended": False})
            if rec.get("event_type") == "terminated":
                row["ended"] = True
            if row["from"] is None and rec.get("event_type") == "message":
                env = rec.get("envelope") or {}
                row["from"], row["to"] = env.get("from"), env.get("to")
        return list(seen.values())[:50]                  # scan all, THEN slice (don't truncate mid-fill)

    @app.get("/")  # the operator SPA — static page, no auth (actions are token-gated)
    def index():
        path = os.path.join(WEB_DIR, "index.html")
        if os.path.exists(path):
            return FileResponse(path)
        return HTMLResponse("<h1>Aether Operator</h1><p>web/index.html not found</p>")

    return app


def run(host=None, port=None, db=None,
        redis_host=None, redis_port=None,
        token: Optional[str] = None):  # pragma: no cover
    """Launch the operator panel. Binds localhost only by default (§18.3 decision
    1). Every parameter falls back to an ``AETHER_*`` env var then the original
    default, so ``run()`` with no args/env behaves exactly as before. Inside a
    container set ``AETHER_OPERATOR_HOST=0.0.0.0`` and ``AETHER_REDIS_HOST=redis``,
    but publish the host port on 127.0.0.1 only — the privileged write path must
    stay loopback-only at the host boundary, in addition to its token.

    Redis connection goes through the shared resolver (auth/TLS via AETHER_REDIS_*
    env); no direct ``redis.Redis`` build here."""
    import uvicorn
    from ..core.aether_client import make_redis
    from ..core.conn import resolve_redis_kwargs

    host = host or os.environ.get("AETHER_OPERATOR_HOST", "127.0.0.1")
    port = int(port if port is not None else os.environ.get("AETHER_OPERATOR_PORT", 8770))
    token = token or os.environ.get("AETHER_OPERATOR_TOKEN")
    if not token:
        raise SystemExit("set AETHER_OPERATOR_TOKEN (the operator panel requires a token)")
    raw = make_redis(**resolve_redis_kwargs(cli={"host": redis_host, "port": redis_port, "db": db}))
    app = create_operator_app(raw, token)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    run()
