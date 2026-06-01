"""Stargazer — the read-only observatory dashboard (spec §7, §15, §16).

Core invariant (spec §16.1-6): Stargazer is a PURE OBSERVER. It never writes to
Redis, never emits a message, never ACKs, never touches the registry. The
observer can never become an actor — an extension of the §13.5 injection
discipline. That invariant is enforced structurally by :class:`ReadOnlyRedis`
(no write command is even reachable) and proven by a structural test.
"""
