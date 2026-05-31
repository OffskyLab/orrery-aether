"""Operator control plane (spec §18.2) — the system's FIRST write path.

This is a SEPARATE, authenticated service. It is deliberately NOT part of the
read-only Stargazer: folding a write endpoint into Stargazer would put back the
very write path §16.1-6 proves is absent. Stargazer stays zero-write; privileged
human intervention lives here, behind localhost + a bearer token, with every
action audited to aether:events as ``event_type=operator_action``.

Operator privilege is "may initiate / may intervene" — NOT "may bypass the
receiving body's input isolation": an operator-injected message is still treated
as untrusted data by the receiver (§13.5 / §18.2), exactly like any other.
"""
