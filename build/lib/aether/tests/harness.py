"""Deterministic test harness — scripted Claude responders + a message pump.

Responders now return RAW :class:`ClaudeTurn`s (the runner no longer parses),
so the §13.2 parsing seam is exercised even by fast tests. Nothing here touches
the real ``claude`` CLI or the wall clock, so every scenario runs in
milliseconds and is fully repeatable (spec §11.2 / §14.2).
"""
from __future__ import annotations

import json
import uuid
from typing import Callable, List, Optional

from aether.observatory.claude_runner import ClaudeInvocation, ClaudeTurn
from aether.observatory.main import Observatory

Responder = Callable[[ClaudeInvocation], ClaudeTurn]


def _session_for(inv: ClaudeInvocation) -> str:
    # Stable per (project, conversation) so resume bookkeeping has something real.
    return f"sess-{inv.project_id}-{inv.conversation_id[:8]}"


def control_json(reply_needed: bool, *, to: Optional[str] = None,
                 intent: str = "inform", text: str = "(scripted)",
                 summary: str = "") -> str:
    """Render a well-formed control block as Claude would emit it."""
    obj = {
        "reply_needed": reply_needed,
        "to": to,
        "reply_body": ({"intent": intent, "text": text} if reply_needed else None),
        "summary": summary,
    }
    return f"Here is my decision.\n```json\n{json.dumps(obj)}\n```"


def always_reply(*, to: Optional[str] = None, intent: str = "inform",
                 text: str = "(scripted reply)", summary: str = "replied") -> Responder:
    """Always wants to reply — forces a runaway ping-pong only a guard can stop."""

    def _r(inv: ClaudeInvocation) -> ClaudeTurn:
        return ClaudeTurn(raw_text=control_json(True, to=to, intent=intent, text=text,
                                                summary=summary),
                          session_id=_session_for(inv))

    return _r


def never_reply(*, summary: str = "resolved, no reply") -> Responder:
    """Never replies — the natural-convergence end state."""

    def _r(inv: ClaudeInvocation) -> ClaudeTurn:
        return ClaudeTurn(raw_text=control_json(False, summary=summary),
                          session_id=_session_for(inv))

    return _r


def reply_once_then_stop(*, intent: str = "result", text: str = "(scripted answer)",
                         summary: str = "answered") -> Responder:
    state = {"count": 0}

    def _r(inv: ClaudeInvocation) -> ClaudeTurn:
        state["count"] += 1
        if state["count"] == 1:
            raw = control_json(True, intent=intent, text=text, summary=summary)
        else:
            raw = control_json(False, summary="nothing further")
        return ClaudeTurn(raw_text=raw, session_id=_session_for(inv))

    return _r


def crash_once_then(responder: Responder) -> Responder:
    """Raise on the first invocation (simulate a crash mid-Claude-call), then
    behave like ``responder`` (used by Phase 1 scenario 6)."""
    state = {"crashed": False}

    def _r(inv: ClaudeInvocation) -> ClaudeTurn:
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("simulated crash during claude call")
        return responder(inv)

    return _r


# ---- driving the conversation ---------------------------------------------
def drain(obs: Observatory, block_ms: int = 30) -> int:
    total = 0
    while True:
        n = obs.poll_once(block_ms=block_ms, count=20)
        if n == 0:
            return total
        total += n


def pump(observatories: List[Observatory], *, max_rounds: int = 1000,
         block_ms: int = 30) -> int:
    for rounds in range(1, max_rounds + 1):
        moved = sum(drain(o, block_ms=block_ms) for o in observatories)
        if moved == 0:
            return rounds
    raise AssertionError(
        f"conversation did not converge within {max_rounds} rounds — "
        "a guardrail likely failed to stop it"
    )


def new_conversation_id() -> str:
    return str(uuid.uuid4())
