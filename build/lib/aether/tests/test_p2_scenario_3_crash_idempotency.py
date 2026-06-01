"""Phase 2 · Scenario 3 — 崩潰不重複付費／不重複送 (spec §14.1-3, §13.1).

Two crash points, asserted with EXACT counts (spec §14.3):
  (a) kill after CLAUDE_DONE, before the reply → redelivery must NOT call Claude
      again (call_count stays 1) and the reply is emitted exactly once.
  (b) kill after REPLY_EMITTED, before ACK → redelivery must NOT emit any extra
      reply (the derivable reply id makes it idempotent regardless).
"""
import pytest

from aether.core.envelope import derive_reply_id, new_envelope
from aether.core.processing_log import CLAUDE_DONE, REPLY_EMITTED
from .harness import always_reply


def _inbound():
    return new_envelope(from_="project_alpha", to="project_beta",
                        intent="ask", text="answer exactly once, survive a crash")


def test_crash_after_claude_done_does_not_repay(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")  # reply target online
    obs, crash = make_p2_obs("project_beta", always_reply(text="the answer"))

    env = _inbound()
    client.emit(env)
    crash.crash_after(CLAUDE_DONE)

    with pytest.raises(RuntimeError, match="injected crash"):
        obs.poll_once(block_ms=100)
    assert obs.runner.call_count == 1            # Claude was paid once
    assert client.r.xlen("aether:inbox:project_alpha") == 0  # reply not sent yet

    # Restart → recover the pending message.
    obs.recover_pending(min_idle_ms=0)
    assert obs.runner.call_count == 1            # EXACTLY one — no re-pay
    assert client.r.xlen("aether:inbox:project_alpha") == 1  # reply sent exactly once

    # the reply uses the derivable id (end-to-end idempotent)
    _id, fields = client.r.xrange("aether:inbox:project_alpha")[0]
    import json
    reply = json.loads(fields["data"])
    assert reply["message_id"] == derive_reply_id(env.message_id)


def test_crash_after_reply_emitted_does_not_double_send(make_p2_obs, client, heartbeat):
    heartbeat.beat("project_alpha")
    obs, crash = make_p2_obs("project_beta", always_reply(text="the answer"))

    env = _inbound()
    client.emit(env)
    crash.crash_after(REPLY_EMITTED)

    with pytest.raises(RuntimeError, match="injected crash"):
        obs.poll_once(block_ms=100)
    assert obs.runner.call_count == 1
    assert client.r.xlen("aether:inbox:project_alpha") == 1  # reply already out once

    obs.recover_pending(min_idle_ms=0)
    assert obs.runner.call_count == 1                         # no re-pay
    assert client.r.xlen("aether:inbox:project_alpha") == 1   # NOT double-sent
