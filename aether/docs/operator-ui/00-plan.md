# Plan — Operator control UI (+ remove/unregister body) (seed for discussion)

> Goal (user, verbatim intent): 一個 **operator 的操作畫面(web UI)**,涵蓋**所有** operator
> 操作,**外加**剛討論到的「移除一個 registry body(remove id)」。流程
> `/plan → /discuss(≤3 輪)→ /write-spec → /audit-spec → 實作`;每步留紀錄;同一 bug 修 >3 次就停下求助。

Planning SEED — ground the design in verified facts + surface the blind spots for `/discuss`.
Do NOT lock decisions here.

## Verified technical facts (checked in code this session)

- **operator panel exists** as `aether/operator_panel/server.py` — a FastAPI **token-gated write
  API**, built with a **WRITABLE** Redis (`create_operator_app(redis, token)`), bound localhost
  only (§18.3). Endpoints today: `GET /health` (no auth), `POST /inject|/pause|/resume|/terminate|
  /kill_project` (each `Depends(require_token)`, bearer token). It has **no HTML UI** and **no
  `web/` dir**. `app.state.service` is an `OperatorService`.
- **`OperatorService`** (`operator_panel/control_service.py`): `inject / pause / resume / terminate /
  kill_project`, `actor=identity`; every op calls `client.emit_operator_action(...)` → audited on
  `aether:events` (§18.2). **No remove/unregister yet.**
- `kill_project` only sets a control flag (`control.kill_project` → `redis.set(project_kill_key,"1")`);
  it does **not** delete the body from the registry.
- **`Registry.remove(project_id)`** exists (`hdel REGISTRY_KEY`). `Registry.all()` reads the table.
- **Stargazer** serves a **single-file SPA** at `GET /` via `FileResponse(WEB_DIR/index.html)`
  (`stargazer/server.py`); `WEB_DIR = stargazer/web/`. It is **strictly read-only** (`ReadOnlyRedis`)
  — a hard, test-guarded invariant.
- Connection now flows through `resolve_redis_kwargs` (cross-machine), and the operator `run()` uses
  it; so the operator panel can point at a remote (auth+TLS) bus.

## Current state — how operator is used today

There is **no clickable UI**. Operators drive it two ways: (a) authenticated HTTP to `:8770`
(`curl -H "authorization: Bearer <token>" -X POST …`); (b) from Claude Code via the MCP
`aether_control(thread, action)` tool / `/mcp__aether__stop`. The user wants a **web screen** that
exposes every op + a new **remove body**.

## The capability — decomposition

### A. New write op: `unregister` (remove a body from the registry)
- `OperatorService.unregister(project_id)` → `Registry(redis).remove(project_id)` + audit
  `emit_operator_action(actor, "unregister", project_id=…)`.
- `POST /unregister` (token-gated), body `{project_id}`. Returns `{project_id, state:"removed"}`.
- Semantics question (for discuss): registry-only `hdel`, or also clean the body's `aether:inbox:<id>`
  / heartbeat / hold keys? A body whose Observatory is still running would re-appear on its next
  `register_body` — should we warn/refuse if it's currently online?

### B. The UI (a single-file SPA served by the operator panel)
- Serve `GET /` (no auth — static page) from `operator_panel/web/index.html`, mirroring Stargazer's
  `FileResponse` pattern. Vanilla HTML+JS, no build step.
- The page provides controls for **all** ops: inject (to/intent/text/conversation_id?/solicit/
  max_hops), pause/resume/terminate (conversation_id), kill_project (project_id), **unregister**
  (project_id). Destructive ops (terminate / kill_project / unregister) are **confirm-first**.
- To pick targets, the UI needs **read** data: a body list (id + online) and recent conversations/
  threads. Add small **GET** read endpoints to the operator panel (it already holds a redis client):
  e.g. `GET /api/bodies`, `GET /api/conversations` (or reuse the event timeline). These are reads on
  the WRITE service — NOT subject to Stargazer's read-only invariant (that invariant is about
  Stargazer specifically).
- **Token handling**: the page has a token field (paste once → kept in `sessionStorage`/memory),
  sent as `Authorization: Bearer …` on every action POST. The page load (GET /) is unauthenticated.

## Recommended design (pre-discussion)
1. Keep the UI **on the operator panel** (self-served at `:8770`), NOT in Stargazer — preserves the
   read(Stargazer)/write(operator) separation and the read-only invariant.
2. Single-file SPA (`operator_panel/web/index.html`), served via `FileResponse`, vanilla JS.
3. Add `unregister` op + `POST /unregister` + the minimal `GET` read endpoints the UI needs.
4. Confirm-first for destructive actions; every action audited (existing pattern + new unregister).
5. Token: paste field → sessionStorage → Bearer header. localhost-only exposure unchanged.

## Open questions for /discuss (≤3 rounds)
1. **UI location**: operator-panel-self-served (recommended) vs add to Stargazer SPA (risks blurring
   read-only) vs a separate static host?
2. **`unregister` scope**: registry `hdel` only, or also purge inbox/heartbeat/hold/proclog keys?
   Refuse/warn when the body is currently online (heartbeating)?
3. **Read endpoints on the write service**: acceptable, or should the UI read from Stargazer's API
   instead (cross-port)? What minimal read surface does the UI actually need?
4. **Browser token UX/security**: paste field + sessionStorage vs prompt-per-action vs a `?token=`
   query (worse). CSRF (token in header, no cookies → low risk) — confirm.
5. **Confirm-first** scope: which ops require an explicit confirm step in the UI.
6. Should `GET /` (the page) itself be token-gated, or stay open (localhost-only, actions gated)?

## Affected files (anticipated)
- `aether/operator_panel/control_service.py` — `unregister()`.
- `aether/operator_panel/server.py` — `POST /unregister`, `GET /` (FileResponse), `GET /api/bodies`
  + `GET /api/conversations` (reads), import Registry.
- `aether/operator_panel/web/index.html` (NEW) — the single-file SPA.
- `aether/tests/test_p4_operator.py` (or a new test) — unregister endpoint + read endpoints + auth.
- READMEs (operator UI section), `DEVLOG.md`.

## Risks & non-goals
- **Non-goal**: changing Stargazer (no write path added to the read-only dashboard).
- **Non-goal**: a new auth scheme (keep the existing bearer token; no user accounts/sessions).
- **Non-goal**: exposing the panel beyond localhost (still localhost+token; LAN via SSH tunnel only).
- **Risk**: serving a UI that holds the token in the browser — keep localhost-only; never log it.
- **Risk**: `unregister` of an online body (re-registers) — handle/Warn.
- **Risk**: read endpoints on the write service must not weaken the token gate on writes.

## Draft acceptance criteria (firm up in /write-spec)
- AC1: `POST /unregister` (token) removes the body from the registry + emits an audited
  `operator_action`; without token → 401.
- AC2: `GET /` serves the operator SPA; the page exposes inject/pause/resume/terminate/kill_project/
  unregister; destructive ops confirm-first.
- AC3: `GET /api/bodies` lists bodies + online status (token or open? per discuss); UI can populate.
- AC4: existing 105 tests stay green; Stargazer read-only invariant test unchanged & green.
- AC5: localhost-only exposure preserved; token still required for every write incl. unregister.
