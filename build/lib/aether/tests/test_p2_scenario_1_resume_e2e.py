"""Phase 2 · Scenario 1 — 多跳 session resume（真實）(spec §14.1-1).

Real ``claude -p``. A multi-message conversation to a real backend Body across
the same conversation_id; assert that Body reuses ONE session_id across its turns
(real ``--resume``), that conversation_id is stable, and that it stays within
Horizon. Gated behind --run-e2e.
"""
import os
import uuid

import pytest

from aether.core.clock import SystemClock
from aether.core.envelope import new_envelope
from aether.core.guards import RateLimiter
from aether.core.heartbeat import Heartbeat
from aether.core.processing_log import ProcessingLog
from aether.core.session_store import SessionStore
from aether.observatory.claude_runner import RealClaudeRunner
from aether.observatory.main import Observatory
from .harness import drain

BETA_DIR = "/tmp/aether-p2/project_beta"


def _seed_beta():
    os.makedirs(BETA_DIR, exist_ok=True)
    with open(os.path.join(BETA_DIR, "orders_api.md"), "w") as f:
        f.write(
            "# Orders API (project_beta)\n\n"
            "GET /api/orders/{id} returns an order object whose unique identifier\n"
            "field is `order_id` (a UUID string). The list endpoint GET /api/orders\n"
            "returns an array of orders, each item also carrying `order_id`.\n"
        )


@pytest.mark.e2e
def test_multihop_session_resume_real(r, client, registry):
    _seed_beta()
    hb = Heartbeat(r, ttl_seconds=600, clock=SystemClock())
    hb.beat("project_alpha")  # so B's replies are deliverable, not held
    hb.beat("project_beta")

    runner = RealClaudeRunner()
    obs_b = Observatory(
        "project_beta", client, runner,
        RateLimiter(redis=r, max_per_window=100, clock=SystemClock()),
        ProcessingLog(r), session_store=SessionStore(r, "project_beta"),
        registry=registry, heartbeat=hb, working_dir=BETA_DIR,
    )

    cid = str(uuid.uuid4())
    client.emit(new_envelope(
        from_="project_alpha", to="project_beta", intent="ask",
        text="What is the exact JSON field name for an order's unique identifier?",
        conversation_id=cid))
    drain(obs_b, block_ms=500)

    client.emit(new_envelope(
        from_="project_alpha", to="project_beta", intent="ask",
        text="Thanks. Is that same field also returned by the list endpoint?",
        conversation_id=cid))
    drain(obs_b, block_ms=500)

    recs = [i for i in runner.invocations if i.project_id == "project_beta"]
    assert len(recs) >= 2, recs
    assert all(i.conversation_id == cid for i in recs)           # conversation stable
    sids = {i.session_id for i in recs}
    assert None not in sids and len(sids) == 1, f"session not reused: {sids}"
    assert recs[0].resume is None                                # first turn: new session
    assert recs[1].resume == recs[0].session_id                 # later turns: --resume it

    # stayed within Horizon
    hops = [e["envelope"]["hop_count"] for e in client.read_events() if e["kind"] == "message"]
    assert max(hops) < 8
