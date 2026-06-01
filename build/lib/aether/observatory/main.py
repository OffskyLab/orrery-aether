"""Observatory main loop & processing pipeline (spec §5.2 + §13).

Phase 2 pipeline, built around the §13.1 processing-log state machine:

    RECEIVED → CLAUDE_DONE → REPLY_EMITTED → ACKED

A redelivery after a crash resumes from the last durable state — so Claude is
never re-invoked once CLAUDE_DONE is logged, and a reply is never re-emitted once
REPLY_EMITTED is logged (and is keyed by a derivable id, so even a duplicate
emission is deduped end-to-end). Layered on top: defensive output parsing with a
bounded retry then fail-safe (§13.2), persisted session resume + strict
per-conversation serialization (§13.3), registry-validated routing + heartbeat
online-check with offline hold (§13.4 / §13.6), and injection-isolated prompts
(§13.5).

All the Phase-2 collaborators are optional; with them absent the Observatory
behaves exactly as in Phase 1 (direct reply-to-sender, in-memory session, no
crash injection), which keeps the Phase 1 acceptance suite valid.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional

from ..core.aether_client import AetherClient, BROADCAST_STREAM, inbox_stream
from ..core.envelope import BROADCAST, Envelope, derive_reply_id, make_reply
from ..core.guards import RateLimiter, horizon_reached
from ..core.processing_log import (ACKED, CLAUDE_DONE, ProcessingLog, RECEIVED,
                                   REPLY_EMITTED)
from ..core.session_store import InMemorySessionStore
from .claude_runner import ClaudeInvocation, ClaudeRunner
from .control import ControlParseError, ParsedControl, parse_control
from .crash import NullCrashController
from .prompt import build_prompt
from .register import register_gate


class Observatory:
    def __init__(
        self,
        project_id: str,
        client: AetherClient,
        runner: ClaudeRunner,
        rate_limiter: RateLimiter,
        proclog: ProcessingLog,
        *,
        session_store=None,
        registry=None,
        heartbeat=None,
        crash_controller=None,
        control_plane=None,
        offline_policy: str = "hold",
        malformed_retries: int = 1,
        empty_content_lint: bool = True,
        register_policy=None,
        working_dir: Optional[str] = None,
        group: Optional[str] = None,
        consumer: Optional[str] = None,
        subscribe_broadcast: bool = False,
    ) -> None:
        self.project_id = project_id
        self.client = client
        self.runner = runner
        self.rate = rate_limiter
        self.proclog = proclog
        self.session_store = session_store or InMemorySessionStore()
        self.registry = registry
        self.heartbeat = heartbeat
        self.crash = crash_controller or NullCrashController()
        self.control = control_plane  # §18.2 operator pause/resume/terminate (read side)
        self.offline_policy = offline_policy
        self.malformed_retries = malformed_retries
        self.empty_content_lint = empty_content_lint
        # Relationship-based register (§17.3): (from_pid, to_pid) → "concise"|"critical".
        # Default → concise service register for every relationship.
        self.register_policy = register_policy or (lambda _f, _t: "concise")
        self.working_dir = working_dir
        self.group = group or f"grp-{project_id}"
        self.consumer = consumer or project_id
        self.inbox = inbox_stream(project_id)
        self.subscribe_broadcast = subscribe_broadcast

        # Scope the idempotency log + per-conversation lock to THIS body. A
        # broadcast Wave is a single message_id read by N bodies (each via its own
        # consumer group); per-body keys mean each body processes its own copy
        # exactly once (§18.1 fan-out) instead of N-1 bodies dedup-skipping it.
        # (Restart recovery is unaffected: both instances of one body share a
        # project_id → share the namespace.)
        self.proclog.prefix = f"aether:proclog:{project_id}"

        self.client.ensure_group(self.inbox, self.group)
        if self.subscribe_broadcast:
            self.client.ensure_group(BROADCAST_STREAM, self.group)

    # ---- streams -----------------------------------------------------------
    def _read_map(self) -> dict:
        m = {self.inbox: ">"}
        if self.subscribe_broadcast:
            m[BROADCAST_STREAM] = ">"
        return m

    def _all_streams(self) -> List[str]:
        return [self.inbox] + ([BROADCAST_STREAM] if self.subscribe_broadcast else [])

    # ---- the pipeline ------------------------------------------------------
    def process(self, env: Envelope) -> None:
        mid = env.message_id

        # §18.1: never process your own broadcast (a Wave you originated). Avoids
        # a body replying to its own announcement.
        if env.type == "wave" and env.from_ == self.project_id:
            return

        # §18.2 operator control: kill drops, pause holds — checked before any
        # other work so an operator can stop a runaway conversation immediately.
        if self.control is not None:
            if (self.control.is_killed(env.conversation_id)
                    or self.control.is_project_killed(self.project_id)):
                self.proclog.mark(mid, ACKED, reason="operator_kill")
                self.client.emit_event("terminated", env, reason="operator_kill")
                return
            if self.control.is_paused(env.conversation_id):
                # Park the inbound; it is redelivered (no re-mirror) on resume.
                self.client.hold_inbound(self.project_id, env)
                return

        state = self.proclog.state(mid)

        # Already fully handled (a genuine duplicate delivery) → skip (idempotent).
        if state == ACKED:
            self.client.emit_event("duplicate_skipped", env, reason="dedup")
            return

        # Guards only run on first encounter; a redelivery has already passed them.
        if state is None:
            if horizon_reached(env):
                self.proclog.mark(mid, ACKED, reason="horizon")
                self.client.emit_event("terminated", env, reason="horizon")
                return
            if self.rate.exceeded(env.conversation_id):
                self.proclog.mark(mid, ACKED, reason="rate_limited")
                self.client.emit_event("terminated", env, reason="rate_limited")
                return
            self.proclog.mark(mid, RECEIVED)
            self.crash.check(mid, RECEIVED)

        # Strict serialization per conversation_id (§13.3) — no three-body chaos.
        with self._conversation_lock(env.conversation_id):
            self._process_locked(env)

    def _process_locked(self, env: Envelope) -> None:
        mid = env.message_id

        # ── CLAUDE_DONE phase ──
        if not self.proclog.at_least(mid, CLAUDE_DONE):
            # ``project`` records WHICH body is processing — essential for a Wave,
            # whose envelope ``to`` is "broadcast" not the processing body.
            self.client.emit_event("processing_start", env, project=self.project_id)
            control = self._produce_control(env)
            control = self._apply_wave_policy(env, control)  # §18.1 fan-out control
            self.proclog.save_claude_result(
                mid, control.to_dict(), session_id=control.session_id,
                reply_message_id=derive_reply_id(mid),
            )
            if control.session_id:
                self.session_store.set(env.conversation_id, control.session_id)
            self.client.emit_event("processing_done", env, summary=control.summary,
                                   reason=control.reason, project=self.project_id)
            if control.reason == "malformed_output":
                self.client.emit_event("malformed_output", env, reason="malformed_output")
            self.crash.check(mid, CLAUDE_DONE)  # state is durable before this point
        else:
            control = ParsedControl.from_dict(self.proclog.load_claude_result(mid) or {})

        # ── REPLY_EMITTED phase ──
        if not self.proclog.at_least(mid, REPLY_EMITTED):
            if control.reply_needed:
                # §17.1 register gate: an ack/pleasantry reply never leaves —
                # silence == acknowledgment. Suppress → log, do NOT deliver.
                gate = register_gate(control, empty_content_lint=self.empty_content_lint)
                if gate:
                    to = control.to if control.to is not None else env.from_
                    self.client.emit_event("ack_suppressed", env,
                                           reason="ack_suppressed", to=to, gate=gate)
                else:
                    self._deliver_reply(env, control)
            self.proclog.mark(mid, REPLY_EMITTED)
            self.crash.check(mid, REPLY_EMITTED)

        self.proclog.mark(mid, ACKED)

    # ---- Claude turn: run → parse → bounded retry → fail-safe (§13.2) ------
    def _produce_control(self, env: Envelope) -> ParsedControl:
        turn = self.runner.run(self._make_inv(env, reoutput=False))
        try:
            return parse_control(turn.raw_text, session_id=turn.session_id)
        except ControlParseError:
            pass

        session_id = turn.session_id
        for _ in range(self.malformed_retries):  # strictly bounded
            retry = self.runner.run(self._make_inv(env, reoutput=True, resume=session_id))
            session_id = retry.session_id or session_id
            try:
                return parse_control(retry.raw_text, session_id=session_id)
            except ControlParseError:
                continue

        # Fail-safe: stay silent rather than send garbage or crash-loop (§13.2).
        return ParsedControl.fail_safe(session_id=session_id)

    def _make_inv(self, env: Envelope, *, reoutput: bool,
                  resume: Optional[str] = None) -> ClaudeInvocation:
        if resume is None:
            resume = self.session_store.get(env.conversation_id)
        register = self.register_policy(env.from_, self.project_id)  # §17.3
        prompt = build_prompt(
            env, self.project_id,
            registry=self._registry_view(), online=self._online_view(),
            reoutput_only=reoutput, register=register,
        )
        return ClaudeInvocation(prompt=prompt, working_dir=self.working_dir,
                                resume=resume, project_id=self.project_id,
                                conversation_id=env.conversation_id)

    # ---- Wave fan-out anti-explosion (§18.1) -------------------------------
    def _apply_wave_policy(self, env: Envelope, control: ParsedControl) -> ParsedControl:
        """A Wave is an announcement, not a conversation opener. Unless it
        explicitly solicits, force reply_needed=false — the primary fan-out
        anti-explosion control (§18.1-1). A solicited Wave's reply is handled in
        _deliver_reply (directed Comet to the originator, never broadcast)."""
        if env.type == "wave" and not env.solicit:
            control.reply_needed = False
            control.reply_body = None
            if not control.reason:
                control.reason = "wave_announcement"
        return control

    # ---- routing: validate recipient + online-check + hold (§13.4 / §13.6) -
    def _deliver_reply(self, env: Envelope, control: ParsedControl) -> None:
        body = control.reply_body or {}
        is_solicited_wave = env.type == "wave" and env.solicit
        if is_solicited_wave:
            # §18.1: a solicited Wave's response is ALWAYS a directed Comet back
            # to the originator — never broadcast, never to a model-chosen third
            # party. The originator collects and decides what to do next.
            to = env.from_
        else:
            to = control.to if control.to is not None else env.from_

        # §18.1 hard guard: a reply is NEVER a Wave (broadcast-storm prevention).
        if to == BROADCAST:
            self.client.emit_event("reply_rejected", env, reason="wave_reply_forbidden", to=to)
            return

        reply = make_reply(
            env, from_=self.project_id, to=to,
            intent=body.get("intent", "inform"), text=body.get("text", ""),
            context=body.get("context"),
            message_id=derive_reply_id(env.message_id),  # idempotent reply id
        )

        # Claude must only ever route to a known Body; reject + log otherwise.
        # (A solicited-Wave reply targets the originator by protocol, not by model
        # choice, so it is exempt from the invent-a-recipient check.)
        if (not is_solicited_wave and self.registry is not None
                and not self.registry.has(to)):
            self.client.emit_event("reply_rejected", env, reason="invalid_recipient", to=to)
            return

        # Hold for an offline target; it will be flushed when the target returns.
        if self.heartbeat is not None and not self.heartbeat.is_online(to):
            self.client.hold(reply)
            self.client.emit_event("held", env, reason="recipient_offline", to=to,
                                   held_message_id=reply.message_id)
            return

        self.client.emit(reply)

    def flush_hold(self) -> List[str]:
        """Deliver messages that were held while this Body was offline (§13.6)."""
        moved = []
        for env in self.client.drain_hold(self.project_id):
            self.client.emit(env)  # → own inbox + mirror to aether:events
            moved.append(env.message_id)
        return moved

    def flush_paused(self) -> List[str]:
        """Redeliver inbound messages parked while a conversation was paused
        (§18.2). Called after the operator resumes; messages whose conversation
        is still paused will simply be re-parked when reprocessed."""
        return [env.message_id for env in self.client.flush_inbound_hold(self.project_id)]

    # ---- views injected into the prompt ------------------------------------
    def _registry_view(self) -> Optional[Dict[str, dict]]:
        if self.registry is None:
            return None
        return {pid: {"description": b.description, "capabilities": b.capabilities}
                for pid, b in self.registry.all().items()}

    def _online_view(self) -> Optional[Dict[str, bool]]:
        if self.registry is None or self.heartbeat is None:
            return None
        return {pid: self.heartbeat.is_online(pid) for pid in self.registry.all()}

    # ---- per-conversation lock ---------------------------------------------
    @contextmanager
    def _conversation_lock(self, conversation_id: str):
        # Scoped per body too: a Wave shares one conversation_id across N bodies,
        # and different bodies (different sessions) must not serialize against
        # each other — only same-body same-conversation must (§5.3 / §18.1).
        lock = self.client.r.lock(f"aether:lock:conv:{self.project_id}:{conversation_id}",
                                  timeout=30, blocking_timeout=15)
        acquired = lock.acquire()
        try:
            yield
        finally:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    pass  # already expired / not owned after a simulated crash

    # ---- driving the loop --------------------------------------------------
    def poll_once(self, block_ms: int = 1000, count: int = 10) -> int:
        entries = self.client.read_group(self.group, self.consumer, self._read_map(),
                                          count=count, block_ms=block_ms)
        for stream, entry_id, env in entries:
            self.process(env)
            self.client.ack(stream, self.group, entry_id)  # 處理完才 ACK
        return len(entries)

    def recover_pending(self, min_idle_ms: int = 0) -> List[str]:
        handled: List[str] = []
        for stream in self._all_streams():
            for entry_id, env in self.client.claim_pending(
                stream, self.group, self.consumer, min_idle_ms=min_idle_ms
            ):
                self.process(env)
                self.client.ack(stream, self.group, entry_id)
                handled.append(env.message_id)
        return handled

    def run_forever(self, block_ms: int = 5000) -> None:  # pragma: no cover
        self.recover_pending()
        self.flush_hold()
        while True:
            if self.heartbeat is not None:
                self.heartbeat.beat(self.project_id)
            self.flush_hold()
            self.flush_paused()  # redeliver anything the operator has un-paused
            self.poll_once(block_ms=block_ms)
