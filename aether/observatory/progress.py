"""Live-telescope progress forwarding (spec §15.3).

`claude -p` is a black box; to let Stargazer show what a Body is doing *right now*,
the Observatory side forwards the milestones of the ``stream-json`` event flow
onto ``aether:events`` as ``event_type=progress`` records, live as they arrive.

§15.6 decision 1: forward only MILESTONES by default (turn_start / tool_use /
turn_done); full verbatim text is behind a toggle (``verbatim=True``) to avoid an
event-volume / mirroring-cost explosion.

This lives on the PRODUCER side (it writes to aether:events) — Stargazer itself
stays strictly read-only. A :class:`ProgressForwarder` is what you pass as
``RealClaudeRunner(event_sink=...)``.
"""
from __future__ import annotations

from typing import Any


class ProgressForwarder:
    def __init__(self, client, verbatim: bool = False) -> None:
        self.client = client
        self.verbatim = verbatim

    def __call__(self, project_id: str, conversation_id: str, evt: dict) -> None:
        t = evt.get("type")
        if t == "system" and evt.get("subtype") == "init":
            self.client.emit_progress(conversation_id, project_id, "turn_start")
        elif t == "assistant":
            self._handle_assistant(project_id, conversation_id, evt)
        elif t == "result":
            self.client.emit_progress(conversation_id, project_id, "turn_done",
                                      subtype=evt.get("subtype"))

    def _handle_assistant(self, project_id: str, conversation_id: str, evt: dict) -> None:
        content = (evt.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                self.client.emit_progress(conversation_id, project_id, "tool_use",
                                          name=block.get("name", "?"))
            elif btype == "text" and self.verbatim:
                # Behind the toggle: forward the verbatim assistant text too.
                self.client.emit_progress(conversation_id, project_id, "text",
                                          text=block.get("text", ""))


class NullProgressForwarder:
    """No-op sink (telescope off)."""

    def __call__(self, project_id: str, conversation_id: str, evt: dict) -> None:
        return
