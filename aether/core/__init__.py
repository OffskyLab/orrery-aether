"""Aether core — the message envelope, three-layer guardrails, and the Redis
transport client. This package is the *brain* of the safety story and is
deliberately free of any dependency on the ``claude`` CLI (spec §11.2):
guardrail + routing logic must be testable without ever calling Claude.
"""
