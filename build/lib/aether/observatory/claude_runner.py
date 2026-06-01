"""The injectable Claude boundary (spec §11.2, extended for §13.2).

A runner's job is now narrowly to *run one Claude turn and return the raw text*
— it does NOT parse the control block. Parsing + the fail-safe policy live in
``observatory.control`` / the pipeline, so the fragile seam (§13.2) is always
exercised, even by fast fakes. Nothing under ``core/`` imports this module.

``RealClaudeRunner`` wraps ``claude -p``; ``FakeClaudeRunner`` returns scripted
raw turns. Both record every invocation (resume + returned session_id) and a
call counter, which scenario 3 uses to assert "Claude was invoked exactly once".
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol


@dataclass
class ClaudeInvocation:
    """Everything a runner is asked to act on."""

    prompt: str
    working_dir: Optional[str]
    resume: Optional[str]
    project_id: str
    conversation_id: str


@dataclass
class ClaudeTurn:
    """The RAW outcome of one Claude turn — unparsed (parsing is downstream)."""

    raw_text: str
    session_id: Optional[str] = None
    raw_events: List[dict] = field(default_factory=list)


@dataclass
class InvocationRecord:
    project_id: str
    conversation_id: str
    resume: Optional[str]
    session_id: Optional[str]


class ClaudeRunner(Protocol):
    def run(self, inv: ClaudeInvocation) -> ClaudeTurn:
        ...


class _RecordingMixin:
    invocations: List[InvocationRecord]

    @property
    def call_count(self) -> int:
        return len(self.invocations)

    def _start_record(self, inv: ClaudeInvocation) -> InvocationRecord:
        # Recorded at the START so a call that crashes mid-flight still counts as
        # an attempt (matters for cost accounting / crash assertions).
        rec = InvocationRecord(project_id=inv.project_id,
                               conversation_id=inv.conversation_id,
                               resume=inv.resume, session_id=None)
        self.invocations.append(rec)
        return rec


# --- real runner ------------------------------------------------------------
class RealClaudeRunner(_RecordingMixin):
    """Wraps the real ``claude -p`` CLI.

    Command (verified against official docs §cli-reference):
        claude -p <prompt> --output-format stream-json --verbose [--resume <sid>]
    stream-json in print mode requires --verbose. We forward each stdout event to
    ``event_sink`` (Stargazer later) and read the final ``result`` event for the
    raw answer text + session_id. Parsing happens downstream (§13.2).
    """

    # Conservative read-only tool set (spec §13.5 / §13.6 decision 3): a
    # message-triggered headless Claude gets read + advisory tools only — no
    # Write/Edit/Bash — so it can never take an irreversible action with no human
    # in the loop. ``--tools`` restricts which built-in tools are even available.
    READ_ONLY_TOOLS = ("Read", "Glob", "Grep")

    def __init__(self, binary: str = "claude", timeout: int = 180,
                 event_sink: Optional[Callable[[str, str, dict], None]] = None,
                 extra_args: Optional[List[str]] = None,
                 read_only: bool = True,
                 allowed_tools: Optional[List[str]] = None) -> None:
        self.binary = binary
        self.timeout = timeout
        self.event_sink = event_sink
        self.extra_args = extra_args or []
        self.read_only = read_only
        self.allowed_tools = list(allowed_tools) if allowed_tools is not None \
            else list(self.READ_ONLY_TOOLS)
        self.invocations: List[InvocationRecord] = []

    def run(self, inv: ClaudeInvocation) -> ClaudeTurn:
        rec = self._start_record(inv)
        cmd = [self.binary, "-p", inv.prompt,
               "--output-format", "stream-json", "--verbose"]
        if self.read_only:
            cmd += ["--tools", ",".join(self.allowed_tools)]
        if inv.resume:
            cmd += ["--resume", inv.resume]
        cmd += self.extra_args

        proc = subprocess.Popen(cmd, cwd=inv.working_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        raw_events: List[dict] = []
        result_text = ""
        session_id: Optional[str] = None
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw_events.append(evt)
                if self.event_sink:
                    self.event_sink(inv.project_id, inv.conversation_id, evt)
                if evt.get("session_id"):
                    session_id = evt["session_id"]
                if evt.get("type") == "result":
                    result_text = str(evt.get("result", ""))
            proc.wait(timeout=self.timeout)
        finally:
            if proc.poll() is None:
                proc.kill()
        if proc.returncode not in (0, None):
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"claude exited {proc.returncode}: {stderr[:500]}")

        turn = ClaudeTurn(raw_text=result_text, session_id=session_id, raw_events=raw_events)
        rec.session_id = session_id
        return turn


# --- fake runner (tests) ----------------------------------------------------
class FakeClaudeRunner(_RecordingMixin):
    """Scripted, deterministic stand-in returning RAW turns.

    ``responder`` maps a :class:`ClaudeInvocation` to a :class:`ClaudeTurn` whose
    ``raw_text`` is whatever the test wants the model to have "said" — a valid
    control JSON block, or malformed prose for the fail-safe test.
    """

    def __init__(self, responder: Callable[[ClaudeInvocation], ClaudeTurn]) -> None:
        self.responder = responder
        self.invocations: List[InvocationRecord] = []

    def run(self, inv: ClaudeInvocation) -> ClaudeTurn:
        rec = self._start_record(inv)
        turn = self.responder(inv)  # may raise (simulated crash) — attempt still counted
        rec.session_id = turn.session_id
        return turn
