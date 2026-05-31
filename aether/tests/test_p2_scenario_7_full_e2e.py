"""Phase 2 · Scenario 7 — 正常端對端（真實・含路由＋session＋收斂）(spec §14.1-7).

The §9 flow, but registry-routed and with cross-turn resume, using real claude.
A operator task drops into project_alpha; A's real Claude picks a recipient from
the injected registry (route choice), B answers, A resumes its own session to
read the answer and concludes — the conversation converges within Horizon, fully
reconstructable from aether:events. Gated behind --run-e2e.
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
from .harness import pump

ALPHA_DIR = "/tmp/aether-p2/project_alpha"
BETA_DIR = "/tmp/aether-p2/project_beta"


def _seed_dirs():
    os.makedirs(ALPHA_DIR, exist_ok=True)
    os.makedirs(BETA_DIR, exist_ok=True)
    with open(os.path.join(BETA_DIR, "orders_api.md"), "w") as f:
        f.write("# Orders API (project_beta)\n\nThe orders API's unique identifier "
                "field is `order_id` (a UUID string).\n")


@pytest.mark.e2e
def test_full_registry_routed_convergence_real(r, client, registry):
    _seed_dirs()
    hb = Heartbeat(r, ttl_seconds=600, clock=SystemClock())
    for p in ("project_alpha", "project_beta", "project_gamma"):
        hb.beat(p)

    runner = RealClaudeRunner()

    def build(pid, wd):
        return Observatory(
            pid, client, runner,
            RateLimiter(redis=r, max_per_window=100, clock=SystemClock()),
            ProcessingLog(r), session_store=SessionStore(r, pid),
            registry=registry, heartbeat=hb, working_dir=wd)

    obs_a = build("project_alpha", ALPHA_DIR)
    obs_b = build("project_beta", BETA_DIR)

    cid = str(uuid.uuid4())
    client.emit(new_envelope(
        from_="operator", to="project_alpha", intent="task",
        text=("You are project_alpha (frontend). To render an order-summary card you need "
              "the exact JSON field name your backend's orders API returns for an order's "
              "unique identifier. Choose the most suitable Body from the ones you may contact "
              "and send them a question asking for that field name."),
        conversation_id=cid))

    pump([obs_a, obs_b], max_rounds=12, block_ms=500)

    events = client.read_events()
    # ── route choice: A addressed a Comet to the backend Body ──
    assert any(e["kind"] == "message" and e["envelope"]["to"] == "project_beta"
               for e in events), "A did not route to project_beta"
    # ── converged naturally within Horizon ──
    assert [e for e in events if e["kind"] == "terminated"] == []
    msg_hops = [e["envelope"]["hop_count"] for e in events if e["kind"] == "message"]
    assert max(msg_hops) < 8
    # ── resume: A took >=2 turns in this conversation and reused its session ──
    a_recs = [i for i in runner.invocations
              if i.project_id == "project_alpha" and i.conversation_id == cid]
    assert len(a_recs) >= 2, a_recs
    assert a_recs[0].session_id is not None
    assert a_recs[1].resume == a_recs[0].session_id
