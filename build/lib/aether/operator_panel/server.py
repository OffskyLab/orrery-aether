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
from pydantic import BaseModel

from ..core.aether_client import AetherClient
from ..core.control import ControlPlane
from .control_service import OperatorService


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

    app = FastAPI(title="Aether Operator", description="Authenticated control plane")
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
