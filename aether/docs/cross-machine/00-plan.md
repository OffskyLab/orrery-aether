# Plan — Cross-machine Aether (seed for discussion)

> Goal (user, verbatim intent):
> 1. Redis 端要有**密碼 + TLS** 機制。
> 2. 其他主機能用 `aether register --host 172.16.100.55 --port XXXX` 的方式**註冊**
>    到匯流排（命令形態適不適合，留給 /discuss）。
> 3. client 可以**訂閱 MQ**；當 observer 看到新 queue/訊息時，會**判斷這則訊息是不是
>    自己的**，是的話才往下處理。

This is a planning SEED. Its job is to ground the design in verified facts and to
surface the blind spots that `/discuss` should attack — NOT to lock decisions.

---

## Verified technical facts (grounding — checked in code this session)

- `make_redis(host, port, db)` in `core/aether_client.py` takes **only** host/port/db —
  **no `password`, no `ssl`**. Every entry point (cli `_make_redis`, observatory,
  mcp_server, send_message, consult, stargazer/operator `run()`) funnels through it
  or builds `redis.Redis(...)` the same way, reading `AETHER_REDIS_HOST/PORT/DB`.
- **`working_dir` is NOT in the envelope** — it never travels the wire. It is read
  from `constellation.yaml` by the *local* Observatory and used purely as the
  `claude -p` subprocess `cwd`. ⇒ cross-machine *routing* does not depend on it.
- Routing is **logical, not physical**: messages go to `aether:inbox:<project_id>`
  (a stream name), consumed by group `grp-<project_id>` / consumer `<project_id>`.
  Nothing in the path encodes a machine.
- `Registry.load_and_sync()` → `sync()` does **`redis.delete(REGISTRY_KEY)` then
  re-add all** — i.e. it **wipes the whole registry** and republishes only the
  bodies in the local file. (Multi-machine partial syncs would clobber each other.)
- `docker-compose.yml` runs Redis with AOF, published on **`0.0.0.0:6379`,
  no auth, no TLS**. The operator panel already reads a secret from gitignored
  `aether/.env` (`AETHER_OPERATOR_TOKEN`) — a precedent for secret handling.
- Heartbeat re-adds the Observatory's **own** body each tick via `registry.add`
  (so a wipe partially self-heals routing for online bodies, but not descriptions
  /capabilities of bodies that aren't currently heartbeating).
- Recipient guard: replying to a body **not in the registry** is terminated with
  `invalid_recipient` (observed live). ⇒ any sender must be a registered body.
- Spec scope note (root README): "Redis high-availability and cross-machine
  deployment (**deferred**)." This goal lifts the deferral for deployment (not HA).

## Current state — what is machine-local vs. shared today

| Thing | Today | Cross-machine implication |
|---|---|---|
| Redis (bus) | one container, 0.0.0.0, no auth | **centralized hub already** — others can point at it once it's secured |
| Routing (inbox/group) | logical names | machine-agnostic — works as-is |
| `working_dir` | local path in constellation | each body's Observatory must run where that path is valid |
| `constellation.yaml` | one file, read locally | needs a coherent multi-machine story (single shared vs per-host) |
| Connection params | host/port/db via env | **missing password/TLS** |
| `sync()` | delete-all + republish | **clobbers** if each host syncs a partial file |

---

## The three capabilities — decomposition & options

### F1 — Redis password + TLS  *(clearest; mostly additive)*

**Work:**
- `make_redis(..., password=None, ssl=False, ssl_ca_certs=None, ssl_certfile=None,
  ssl_keyfile=None)`; default `None/False` ⇒ **behaviourally identical to today**.
- New env read in one place + threaded to all entry points:
  `AETHER_REDIS_PASSWORD`, `AETHER_REDIS_TLS` (bool), `AETHER_REDIS_TLS_CA`
  (+ optional client cert/key for mTLS).
- `docker-compose.yml`: `requirepass ${AETHER_REDIS_PASSWORD}` from `.env`;
  enable Redis 7 native TLS (`--tls-port 6380 --tls-cert-file ... --tls-key-file
  ... --tls-ca-cert-file ...`), mount certs. Decide: TLS-only (close 6379) vs
  both ports. Provide a `make-certs` helper (openssl) + document.
- `.env.example`: add `AETHER_REDIS_PASSWORD`. Generate into gitignored `.env`
  (same pattern as the operator token).
- The `ReadOnlyRedis` facade (Stargazer) must pass through the new kwargs —
  read-only invariant unchanged.

**Open:** ACL (per-identity users) vs one shared password? Self-signed CA vs real
cert? TLS-only vs dual-port during migration?

### F2 — `aether register --host <ip> --port <port>`  *(命令形態待討論)*

The user's phrase "讓其他主機 register" bundles **two** distinct things:
(a) tell *this machine* where the remote bus lives (host/port/password/TLS), and
(b) register *this machine's project* as a body so others can address it.

We already have `client setup` = body registration (appends to constellation +
`load_and_sync`) and it **already accepts `--redis-host/--redis-port/--redis-db`**.
So `register` overlaps `client setup`. Options:

- **Option A — new `aether register`** = "join a remote bus + register self +
  **persist** the endpoint" to a local config (`~/.aether/config.toml` or
  `.aether/bus.json`), so later `observatory/send/mcp/who` auto-use it without
  re-typing `--host/--port` every time. (`register` reads naturally for the
  "join the network" mental model.)
- **Option B — extend `client setup`** with `--redis-password/--redis-tls` and add
  endpoint *persistence*; drop a separate verb (fewer concepts).
- **Option C — split**: `aether bus use --host --port --password --tls` (persist
  endpoint only) + existing `client setup` (register body). Clean separation of
  (a) and (b).

**Recommendation (seed):** **C-ish** — separate "point at the bus" from "register a
body", because they have different lifetimes and failure modes; but expose a
convenience `aether register` that does both for the common case. Persisting the
endpoint to a **local, gitignored** config is the key missing primitive either way.

**Blind spots for /discuss:**
- The `--host/--port` is the *bus* endpoint, not the body's address — naming must
  not imply "registering a host as a peer."
- Cross-machine `working_dir`: a body registered from host B carries B's local
  path; A/C must NOT run B's Observatory. Does `register` write working_dir at all
  for remote bodies, or only for the local one?
- `sync()` wipe (above): if B registers by syncing its partial constellation, it
  deletes A/C's bodies. Need additive sync OR a single authoritative constellation
  OR register-via-`registry.add` (one body) instead of `load_and_sync` (whole file).
- Auth to even *connect* for registration presupposes F1 is in place.

### F3 — client subscribes to MQ; observer checks "is this message mine?"

**Important:** this **largely already exists.** Each Observatory subscribes to its
**own** inbox `aether:inbox:<id>` via a consumer group — the *stream name itself is
the "is it mine?" filter*; you cannot receive another body's messages. So a literal
"one shared queue, filter by recipient" model would be a **regression** (loses
per-tenant isolation, consumer-group exactly-once, and the recipient guard).

Re-interpretation that adds real value cross-machine:
- After F2 `register`, the client should **auto-start consuming its own inbox on the
  remote, secured bus** (i.e. spin up / point an Observatory at the remote Redis) —
  "subscribe to MQ" = run the consumer loop against the remote endpoint.
- The "check if it's mine" is inherent (own-inbox), but we can make it **explicit &
  observable**: log/telescope "received msg for <self> on <remote bus>", and keep
  the `invalid_recipient` guard as the safety net.
- Possible genuine new want: a **discovery/announce** channel so a freshly-registered
  host is *seen* by others (the broadcast stream already exists; could carry a
  `registered`/`hello` event).

**Blind spots for /discuss:** Is the user actually asking for (i) "make my existing
inbox subscription work against a remote bus" (yes, valuable), or (ii) "a shared
queue with app-level filtering" (an anti-pattern here)? Confirm before building.
Also: should `register` imply auto-`observatory`, or stay separate (explicit start)?

---

## Recommended design (summary, pre-discussion)

1. **F1 first** (it unblocks F2/F3): password + TLS in `make_redis`, env-threaded,
   compose + certs + `.env`. Fully backward-compatible.
2. **Endpoint persistence** primitive: a local gitignored bus-profile file so
   non-local hosts don't retype connection/secret flags.
3. **F2** as a thin command over (1)+(2)+existing `client setup`; final verb shape
   decided in /discuss. Body registration via `registry.add` (single body) to avoid
   the `sync()` wipe — or make `sync()` additive — TBD in discuss.
4. **F3** = "subscribe = run the consumer against the remote bus"; the own-inbox
   filter already satisfies "is it mine". Add observability + optional announce.
5. **Single source of truth for the star chart** across machines (shared complete
   constellation, OR registry-as-truth with additive registration) — pick in discuss.

## Affected files (anticipated)

- `core/aether_client.py` (`make_redis` signature + env), all `run()`/CLI redis
  builders (`cli.py:_make_redis`, `run_observatory.py`, `mcp_server.py`,
  `send_message.py`, `consult.py`, `stargazer/server.py`, `operator_panel/server.py`).
- `core/registry.py` (`sync` additive option / `register` path).
- `cli.py` (new `register` / `bus use`; endpoint persistence loader).
- `docker-compose.yml`, `.env.example`, new cert helper, `.gitignore`.
- `stargazer/readonly.py` (pass-through new kwargs).
- READMEs (cross-machine section), `DEVLOG.md`, tests.

## Risks & non-goals

- **Non-goal:** Redis HA / clustering / failover (still deferred).
- **Non-goal:** NAT traversal / public-internet exposure hardening beyond
  password+TLS (recommend VPN/tunnel; document, don't build).
- **Risk:** `sync()` wipe causing cross-host registry loss — must be resolved.
- **Risk:** secret sprawl (password + operator token) — keep all in gitignored
  `.env`, never in committed `.mcp.json`/constellation.
- **Risk:** F3 mis-scope (shared-queue anti-pattern) — confirm intent in discuss.
- **Risk:** TLS cert distribution/rotation across hosts — document a simple path.

## Draft acceptance criteria (to be firmed up in /write-spec)

- AC1: with `AETHER_REDIS_PASSWORD`+TLS set, all entry points connect over TLS with
  auth; with neither set, behaviour byte-identical to today (regression-safe).
- AC2: a second host can join the bus and register its body without retyping
  endpoint/secret on every command (persisted profile).
- AC3: registering host B does **not** delete host A/C's bodies from the registry.
- AC4: B↔C exchange a message end-to-end through A's secured bus (own-inbox filter
  proven; cross-host reply received).
- AC5: existing 88 tests stay green; localhost dev path unchanged.

## Open questions to resolve in /discuss (≤3 rounds)

1. F2 verb: `register` (does-both) vs `bus use` + `client setup` (split) vs extend
   `client setup`? Where to persist the endpoint?
2. Star chart across machines: single shared complete constellation, or
   registry-as-truth with **additive** registration (kill the `sync()` wipe)?
3. F3 intent: "remote-bus subscription of my own inbox" (recommended) vs
   "shared queue + app filter" (confirm/reject)? Auto-start observatory on register?
4. Security depth: one shared password vs Redis ACL users; self-signed vs real CA;
   TLS-only vs dual-port migration.
5. `working_dir` for remote bodies: omit/null for non-local, or require path-valid-
   on-its-own-host convention?
