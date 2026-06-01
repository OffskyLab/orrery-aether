"""Observatory — the per-project resident listener (spec §5).

It is deliberately "dumb": receive → build prompt → call Claude → decide whether
to reply, plus enforce the guardrails. All cleverness lives in Claude; all the
safety lives in ``core/``. The one piece that touches Claude — the runner — is
an injectable interface so the whole pipeline is testable without the CLI.
"""
