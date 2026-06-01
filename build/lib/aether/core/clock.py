"""Injectable time source (spec §11.2).

The rate-limit window must be testable in milliseconds without waiting a real
minute, so *time itself* is a dependency. Production uses :class:`SystemClock`;
tests use :class:`ManualClock` and advance it explicitly.
"""
from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:  # unix seconds
        ...


class SystemClock:
    """Real wall-clock time."""

    def now(self) -> float:
        return time.time()


class ManualClock:
    """A clock the test drives by hand. Never advances on its own."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)

    def set(self, seconds: float) -> None:
        self._t = float(seconds)
