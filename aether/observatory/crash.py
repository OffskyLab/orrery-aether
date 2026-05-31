"""Crash injection for tests (spec §14.2).

Lets a test say "die right after the processing log reaches state X" — used to
prove crash-recovery doesn't re-pay for Claude or re-send replies (§14.1-3). The
checkpoint fires AFTER the durable state write, simulating a process that
persisted progress and then died before the next step. Each registration is
one-shot, so the redelivery that follows does NOT crash again.
"""
from __future__ import annotations

from typing import Optional, Set, Tuple


class CrashController:
    def __init__(self) -> None:
        self._armed: Set[Tuple[str, Optional[str]]] = set()
        self._fired: Set[Tuple[str, Optional[str]]] = set()

    def crash_after(self, state: str, message_id: Optional[str] = None) -> None:
        """Arm a one-shot crash after ``state`` (optionally only for one msg)."""
        self._armed.add((state, message_id))

    def check(self, message_id: str, state: str) -> None:
        for key in ((state, message_id), (state, None)):
            if key in self._armed and key not in self._fired:
                self._fired.add(key)
                raise RuntimeError(f"injected crash after state={state}")


class NullCrashController:
    """Default: never crashes."""

    def check(self, message_id: str, state: str) -> None:
        return
