"""Communication register — anti-pleasantry deterministic gates (spec §17.1).

Two agents should talk like focused engineers, not exchange pleasantries. §17
splits this into: things we can ENFORCE in code (deterministic gates, hard
guarantee) and things we can only PROMPT for (strong soft control, in prompt.py).

This module is the hard half (§17.1):
  1. An ``ack`` reply intent never leaves — only ask|inform|task|result (intents
     carrying real payload) are allowed outbound. Silence == acknowledgment, so
     a "got it / thanks" reply is suppressed (reason=ack_suppressed). This cuts
     the "you thank me, I thank you back" spiral at the source.
  3. An optional, CONSERVATIVE empty-content lint: a reply whose text carries no
     question, no new entity/data, and matches a pure-social pattern is
     downgraded too. Conservative on purpose — it would rather let a borderline
     reply through than kill substantive content (no false kills).

Pure logic, no Redis / no Claude. (Horizon + rate, §17.1-4, are the existing
hard floor.)
"""
from __future__ import annotations

import re
from typing import Optional

# §17.1-1: intents with real payload may leave; "ack" (pure acknowledgment /
# thanks / received) must not.
ALLOWED_OUTBOUND_INTENTS = ("ask", "inform", "task", "result")

# Recognizable pure-social phrases (English + 中文). Only used to DOWNGRADE; never
# to invent content.
_SOCIAL_RE = re.compile(
    r"(\bthanks\b|\bthank you\b|\bthx\b|\bappreciate(d)?\b|\bsounds good\b|"
    r"\bsounds great\b|\blooks good\b|\bgreat\b|\bawesome\b|\bperfect\b|"
    r"\bno problem\b|\bno worries\b|\byou'?re welcome\b|\bgot it\b|\bgotcha\b|"
    r"\bwill do\b|\bping me\b|\blet me know\b|\breach out\b|\bcheers\b|"
    r"\breceived\b|\backnowledged\b|\back\b|\bunderstood\b|\bokay\b|\bok\b|"
    r"\bnice\b|\bcool\b|\bhappy to help\b|\bany ?time\b|\bmy pleasure\b|"
    r"if you (ever )?(need|have) (anything|any (questions|help))|"
    r"\bneed anything\b|\bfeel free\b|\bno rush\b)",
    re.IGNORECASE,
)
_SOCIAL_CJK = ("謝謝", "感謝", "多謝", "收到", "了解", "好的", "沒問題", "辛苦了",
               "不客氣", "客氣", "麻煩了", "讚")


def _has_substance(text: str) -> bool:
    """True if the text plausibly carries task-advancing content — be GENEROUS
    here so the lint never kills real content (spec §17.1-3 'conservative')."""
    if "?" in text or "？" in text:          # a concrete question
        return True
    if re.search(r"\d", text):                # any number / datum
        return True
    if "`" in text:                           # code / identifier span
        return True
    if re.search(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_]", text):  # dotted.path
        return True
    if re.search(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b", text):      # snake_case identifier
        return True
    if re.search(r"\b[a-z]+[A-Z][A-Za-z]+\b", text):          # camelCase identifier
        return True
    return False


def is_pure_social(text: Optional[str]) -> bool:
    """True only when the text is recognizably social AND carries no substance
    AND little non-social residue remains. Conservative: defaults to keeping."""
    t = (text or "").strip()
    if not t:
        return True
    if _has_substance(t):
        return False
    has_social = bool(_SOCIAL_RE.search(t)) or any(s in t for s in _SOCIAL_CJK)
    if not has_social:
        return False  # not recognizably social → keep (no false kill)
    residue = _SOCIAL_RE.sub("", t)
    for s in _SOCIAL_CJK:
        residue = residue.replace(s, "")
    residue = re.sub(r"[\s\.,!?;:。，！？、…—\-~()]+", "", residue)
    return len(residue) <= 12  # only trivial non-social text left → pure social


def register_gate(control, *, empty_content_lint: bool = True) -> Optional[str]:
    """Decide whether an outbound reply must be SUPPRESSED (spec §17.1).

    Returns the gate name that fired (``intent_ack`` | ``empty_content_lint``) or
    ``None`` to allow delivery. Only called when control.reply_needed is True."""
    body = control.reply_body or {}
    intent = body.get("intent", "inform")
    if intent not in ALLOWED_OUTBOUND_INTENTS:   # §17.1-1: ack never leaves
        return "intent_ack"
    if empty_content_lint and is_pure_social(body.get("text", "")):  # §17.1-3
        return "empty_content_lint"
    return None
