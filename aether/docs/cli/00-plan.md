# Plan — Unified `aether` CLI (seed for discussion)

> Goal (user, verbatim intent): a unified `aether` CLI such that
> 1. `aether mcp setup` sets up the MCP server for the current project so its
>    capabilities appear in Claude Code;
> 2. `aether client setup` sets up the project's "client" side and connects it to
>    the "server" side;
> 3. in the Claude Code UI, the user can invoke the MCP capabilities via "/"
>    (slash commands) — felt to be more direct than model-decided tool calls.

## Verified technical facts (grounding)
- Claude Code surfaces MCP **prompts** (not tools) as slash commands named
  `/mcp__<server>__<prompt>`. Tools are model-invoked; prompts are user-invoked
  via "/", and a prompt's return value is **injected as a message** the model
  then acts on (a prompt cannot block/await like a tool).
- The SDK's `mcp.server.fastmcp.FastMCP` (used here) supports BOTH `@mcp.tool()`
  and `@mcp.prompt()` on one server; prompt args come from typed params. Verified.
- Project-scoped `.mcp.json` servers require first-use approval; user-scope don't.
  The user may need `/mcp` or a restart to pick up a new server.
- The MCP tools are ASYNC (`aether_ask` returns a thread id; `aether_poll`
  fetches the reply) → an "ask" prompt must inject an instruction that makes the
  model run ask → poll → report.
- `<project>-mcp` identity convention (in `aether/mcp.example.json`) deliberately
  differs from the project's Observatory Body id `<project>` to avoid inbox
  collisions (a resident Observatory would otherwise steal the interactive
  session's replies).

## Current state
- `aether/` is a namespace package; no `pyproject.toml`/`setup.py`/`__init__.py`.
  Everything runs via `python3 aether/<script>.py` or `python3 -m aether.<module>`.
- Separate entry points to unify: `run_observatory.py`, `send_message.py`,
  `consult.py`, `mcp_server.py`, `stargazer.server`, `operator_panel.server`.
- `mcp_server.py` already separates `AetherBridge` (logic) from `build_server`
  (the `@mcp.tool()` wiring) — the seam where `@mcp.prompt()` go.
- `registry.py` only READS `constellation.yaml`; no YAML-writer exists yet.
- Repo is NOT git; 65 tests green (real Redis db15, claude faked).

## Recommended design (summary)
1. **`aether` command**: ship `python3 -m aether.cli` immediately (zero-install,
   non-breaking MVP); add `pyproject.toml` + `console_scripts` for a bare `aether`
   as a follow-up.
2. **`cli.py`**: thin argparse dispatcher reusing each script's `main(argv)` (small
   refactor: accept optional argv). Command tree (MVP): `mcp setup`,
   `client setup`, `server status|up|down`, `observatory <id>`, `send`, `consult`,
   `who`, `mcp serve`.
3. **`aether mcp setup`**: detect project (cwd basename), identity `<project>-mcp`,
   absolute paths, idempotent `.mcp.json` merge (default project scope) OR
   `claude mcp add`; print approve + `/mcp` instructions.
4. **`aether client setup`**: register THIS project as a Body in
   `constellation.yaml` (id/working_dir=cwd; description/capabilities prompted or
   inferred-then-confirmed), ping Redis (offer to start), optional `load_and_sync`;
   guide user to `aether observatory <id>` to go online. Independent of `mcp setup`.
5. **"/" prompts**: add `@mcp.prompt()` in `build_server` mapping the 6 tools →
   `/mcp__aether__{who,ask,discuss,transcript,stop}`. Read-only ops may pre-fetch
   live data at render; async ops inject ask→poll→report instructions.
6. **Change records**: `git init` + per-logical-change commits + `DEVLOG.md`.

## Open questions for the discussion (≤3 rounds)
1. Packaging now (`pyproject` + `__init__.py`, interacts with `pytest.ini`
   `pythonpath=..` + the `sys.path` bootstraps) vs defer (ship `-m aether.cli`)?
2. MCP scope default: **project** (committed, needs approval) vs **user** (no
   approval, not in repo)?
3. `.mcp.json` merge vs `claude mcp add` — support both? default which?
4. Standardize identity `<project>-mcp` (mcp/outbound) vs Body `<project>`
   (client/inbound) — confirm they deliberately differ.
5. Prompts: orchestrate (pre-fetch live data) vs pure template — which per prompt?
6. `client setup` metadata: prompt interactively vs infer description/capabilities
   from `CLAUDE.md`/manifests (and how much silent inference is acceptable)?
7. `constellation.yaml` comment preservation: accept PyYAML comment loss,
   append-only, or add `ruamel.yaml`?
8. Do `client setup`/`mcp setup` imply each other / add an `aether init`? (recommend
   independent)
9. `server up/down` (shell out to docker compose) in scope, or only `status`?
10. Avoid naming clash with `observatory/register.py` (anti-pleasantry register).

## Phasing
0. git init + .gitignore + baseline commit + DEVLOG.md
1. CLI skeleton (`cli.py`, argv refactor, aliases) — non-breaking, 65 tests green
2. `mcp setup` (+ pure `merge_mcp_config`, unit tests)
3. `client setup` (+ pure `merge_constellation`, unit + redis tests)
4. "/" prompts (+ first `build_server` tests, manual live verify)
5. packaging + remaining commands + README
