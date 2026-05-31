"""Structured-output contract + defensive parsing (spec §13.2).

The control block Claude must end its turn with:
    { "reply_needed": bool, "to": string|null,
      "reply_body": {"intent", "text", "context"?}|null }

This is the most fragile seam in the system: a real model may wrap it in prose,
fence it wrong, or drop a field. We extract the last JSON object, validate it
against the schema, and raise :class:`ControlParseError` on any deviation.
The *policy* for what to do on error (one bounded retry, then fail-safe to
"don't reply") lives in the pipeline, not here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from ..core.envelope import INTENTS

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ControlParseError(ValueError):
    """Raised when Claude's output has no valid control block."""


@dataclass
class ParsedControl:
    reply_needed: bool
    to: Optional[str] = None
    reply_body: Optional[dict] = None  # {"intent","text","context"?}
    summary: str = ""
    session_id: Optional[str] = None
    reason: Optional[str] = None  # set to "malformed_output" by the fail-safe path
    raw_events: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reply_needed": self.reply_needed,
            "to": self.to,
            "reply_body": self.reply_body,
            "summary": self.summary,
            "session_id": self.session_id,
            "reason": self.reason,
        }

    @staticmethod
    def from_dict(d: dict) -> "ParsedControl":
        return ParsedControl(
            reply_needed=bool(d.get("reply_needed", False)),
            to=d.get("to"),
            reply_body=d.get("reply_body"),
            summary=d.get("summary", ""),
            session_id=d.get("session_id"),
            reason=d.get("reason"),
        )

    @staticmethod
    def fail_safe(*, session_id: Optional[str], reason: str = "malformed_output") -> "ParsedControl":
        """The §13.2 fail-safe: stay silent, record why."""
        return ParsedControl(reply_needed=False, to=None, reply_body=None,
                             summary="fail-safe: malformed model output, no reply sent",
                             session_id=session_id, reason=reason)


def _balanced_objects(text: str) -> List[str]:
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(text[start:i + 1])
    return out


def _extract_object(text: str) -> dict:
    candidates: List[str] = []
    candidates.extend(_FENCE_RE.findall(text))
    candidates.extend(_balanced_objects(text))
    for chunk in reversed(candidates):  # prefer the LAST JSON object in the turn
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "reply_needed" in obj:
            return obj
    raise ControlParseError("no JSON control object containing 'reply_needed' found")


def parse_control(raw_text: str, *, session_id: Optional[str] = None) -> ParsedControl:
    """Extract + schema-validate the control block, or raise ControlParseError."""
    obj = _extract_object(raw_text)

    rn = obj.get("reply_needed")
    if not isinstance(rn, bool):
        raise ControlParseError("'reply_needed' must be a boolean")

    to = obj.get("to")
    if to is not None and not isinstance(to, str):
        raise ControlParseError("'to' must be a string or null")

    reply_body = obj.get("reply_body")
    if reply_body is not None and not isinstance(reply_body, dict):
        raise ControlParseError("'reply_body' must be an object or null")

    if rn:
        # A reply was requested → it must actually carry self-contained content.
        if not isinstance(reply_body, dict) or not str(reply_body.get("text", "")).strip():
            raise ControlParseError("reply_needed=true requires reply_body.text")
        intent = reply_body.get("intent", "inform")
        if intent not in INTENTS:
            intent = "inform"
        reply_body = {"intent": intent, "text": reply_body["text"],
                      "context": reply_body.get("context") or {}}

    return ParsedControl(
        reply_needed=rn,
        to=to,
        reply_body=reply_body if rn else None,
        summary=str(obj.get("summary", "")),
        session_id=session_id,
    )
