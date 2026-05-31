"""Scenario 6 · 可靠投遞 (spec §11.1.6).

B reads a message and crashes mid-process — before XACK and before marking it
done. On restart, the unACKed (pending) message is reclaimed via the consumer
group, reprocessed, and ACKed. The message is neither lost nor processed to
completion twice.
"""
import pytest

from aether.core.aether_client import AetherClient, inbox_stream
from aether.core.clock import ManualClock
from aether.core.envelope import new_envelope
from aether.core.guards import RateLimiter
from aether.core.processing_log import ACKED, ProcessingLog
from aether.observatory.claude_runner import FakeClaudeRunner
from aether.observatory.main import Observatory
from .harness import crash_once_then, never_reply


def test_unacked_message_redelivered_after_crash(client, r, clock):
    group = "grp-project_beta"
    inbox = inbox_stream("project_beta")

    env = new_envelope(
        from_="project_alpha", to="project_beta", intent="task",
        text="B must process this exactly once, surviving a crash.",
    )
    client.emit(env)

    # ── B instance #1: crashes during the Claude call (after read, before ACK) ──
    runner1 = FakeClaudeRunner(crash_once_then(never_reply()))
    obs_b1 = Observatory(
        "project_beta", client, runner1,
        RateLimiter(redis=r, max_per_window=10_000, clock=clock),
        ProcessingLog(redis=r), consumer="b-consumer-1",
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        obs_b1.poll_once(block_ms=100)

    # The crashed call happened, but nothing completed: no done, message pending.
    assert runner1.call_count == 1
    assert client.pending_count(inbox, group) == 1
    assert [e for e in client.read_events() if e["kind"] == "processing_done"] == []

    # ── B instance #2 restarts and recovers the pending message ──
    runner2 = FakeClaudeRunner(never_reply())
    obs_b2 = Observatory(
        "project_beta", client, runner2,
        RateLimiter(redis=r, max_per_window=10_000, clock=clock),
        ProcessingLog(redis=r), consumer="b-consumer-2",
    )
    handled = obs_b2.recover_pending(min_idle_ms=0)

    # ── the message was redelivered and processed exactly once, nothing pending ──
    assert handled == [env.message_id]
    assert runner2.call_count == 1
    assert client.pending_count(inbox, group) == 0
    done = [e for e in client.read_events() if e["kind"] == "processing_done"]
    assert len(done) == 1
    assert done[0]["message_id"] == env.message_id

    # ── and it is now fully logged: a further redelivery would be skipped, not re-run ──
    assert obs_b2.proclog.state(env.message_id) == ACKED
