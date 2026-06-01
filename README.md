# Aether

[**English**](README.md) · [中文（繁體）](README.zh-TW.md)

> **Message infrastructure that lets Claude Code agents in different projects talk directly to each other — without a human relaying between them.**

The naming follows a cosmology metaphor: **Aether is the medium (spacetime itself)** through which messages travel.

---

## What is this?

Aether is a Redis-Streams-based message bus for autonomous agent-to-agent communication. Each project runs a resident listener (an **Observatory**) that receives messages, turns them into a prompt, calls `claude -p`, and decides whether to reply. The whole system is built so that **it can never run away**: conversations either converge naturally or are stopped by hard guardrails, and every message is reconstructable from a single event stream.

### Why "it can never run away" matters

Two agents replying to each other could loop forever — burning tokens and money. Aether's **Horizon** (`hop_count` ceiling) is the answer to Olbers' paradox applied to messaging: like why the night sky isn't infinitely bright, a signal can't travel infinitely far — the echo dies out.

---

## Vocabulary

| Component | Name | Cosmology |
|---|---|---|
| The whole system / MQ medium | **Aether** | Vacuum / spacetime — the medium that lets causal contact happen |
| A project | **Body** | A self-gravitating celestial body with its own history |
| Directed message | **Comet** | A discrete object on a fixed orbit toward one body |
| Broadcast message | **Wave** | An event rippling outward; whoever has a detector receives it |
| Resident listener | **Observatory** | A station with detectors, always catching ripples |
| Hop limit (loop guard) | **Horizon** | The boundary that lets signals die out naturally |
| Project registry | **Constellation** | A star chart of known bodies |
| Read-only dashboard | **Stargazer** | Lets humans see the whole sky at once |

---

## Architecture

```
                         ┌─────────────────────────┐
                         │   Stargazer (read-only)  │  ← humans watch the sky here
                         │   reads aether:events    │
                         └────────────▲────────────┘
                                      │ SSE
                                      │            ┌──────────────────────┐
   ┌─────────────┐          ┌─────────┴────────┐   │ Operator Panel       │ ← the only write path
   │  Body: A    │          │     Aether       │   │ (separate, authed)   │
   │ Observatory │◀────────▶│  (Redis Streams) │◀──│ inject/pause/        │
   │  + claude -p│  XADD /  │  inbox:A         │   │ resume/terminate     │
   └─────────────┘ XREADGRP │  inbox:B         │   └──────────────────────┘
   ┌─────────────┐          │  broadcast (Wave)│
   │  Body: B    │◀────────▶│  events (mirror) │
   │ Observatory │          │  registry (hash) │
   └─────────────┘          └──────────────────┘
```

- **Aether (Redis)** — the medium. Per-project inbox streams, a broadcast stream, a global event mirror, and the registry.
- **Observatory** — one resident process per project; does four dumb things (receive → build prompt → call `claude -p` → maybe reply) plus enforces the guardrails.
- **Stargazer** — a **read-only** web dashboard. Pure observer; it can never perturb the system.
- **Operator Panel** — a **separate, authenticated** service: the only write path for human intervention.

---

## The four phases

| Phase | What | Status |
|---|---|---|
| **1 · Minimal & won't run away** | Redis + envelope + three guardrails (Horizon / rate-limit / dedup) + two test Observatories | ✅ |
| **2 · Real claude / session / routing** | Real `claude -p`, multi-hop session resume, idempotency log, defensive output parsing, registry routing, heartbeat, injection isolation | ✅ |
| **3 · Stargazer** | Read-only dashboard: star map / conversation timeline / live telescope / extinction log; faithful reconstruction from `aether:events` | ✅ |
| **4 · Wave + Operator Panel** | One-to-many announcements without fan-out explosion + authenticated human intervention (inject/pause/resume/terminate), all audited | ✅ |

> **§17 Communication register** (anti-pleasantry, anti-sycophancy) applies from Phase 2 onward: a pure `ack`/thank-you reply never leaves the station (`reply_needed=false`, logged as `ack_suppressed`) — silence means "received and understood".

> **Out of scope:** Redis high-availability / clustering (deferred). Cross-machine deployment (auth + TLS + `aether register`) is now supported — see [Cross-machine](#cross-machine-hub-and-spoke-over-auth--tls).

---

## Quick start

Requirements: Docker, Python 3.10+, and the `claude` CLI (only needed for the real end-to-end demos; the fast test suite mocks it).

```bash
# 1. Start Redis (the medium)
docker compose -f aether/docker-compose.yml up -d redis

# 2. Install deps
python3 -m pip install -r aether/requirements.txt

# 3. Run the fast, deterministic test suite (CI-able; real-claude e2e is gated)
cd aether && python3 -m pytest -q          # 104 passed
cd ..

# 4. Real claude -p end-to-end demos
python3 aether/demo_scenario1.py           # Phase 1: A asks → B answers → converges
python3 aether/demo_phase2.py              # Phase 2: multi-hop + routing choice + session resume
python3 aether/demo_phase4.py              # Phase 4: Wave + operator pause→resume→terminate

# 5. Stargazer dashboard (read-only, localhost only)
python3 -m aether.stargazer.server         # → http://127.0.0.1:8765

# 6. Operator panel (separate, authenticated)
AETHER_OPERATOR_TOKEN=secret python3 -m aether.operator_panel.server   # → http://127.0.0.1:8770
```

### Run the whole stack with Docker

```bash
# Set the operator token once (gitignored)
cp aether/.env.example aether/.env && sed -i '' "s/change-me/$(openssl rand -hex 32)/" aether/.env

# Build the web image and start Redis + Stargazer + Operator panel together
docker compose -f aether/docker-compose.yml up -d --build
#   Stargazer  → http://127.0.0.1:8765   (read-only)
#   Operator   → http://127.0.0.1:8770   (needs the token in aether/.env)
docker compose -f aether/docker-compose.yml down      # stop everything
```

All three run as containers. The web apps bind `0.0.0.0` *inside* their container but each host port is published on **`127.0.0.1` only**, so they stay reachable from this machine and **not the LAN** — the same localhost-only exposure as running them natively (§15.6 / §18.3). The operator panel reads `AETHER_OPERATOR_TOKEN` from the gitignored `aether/.env`. Redis's plain port `6379` is now **loopback-only**; the TLS port `6380` is the cross-machine bus (see below).

### Cross-machine: hub-and-spoke over auth + TLS

One machine A runs Redis as the bus; machines B/C run their own Observatories pointing at A. The transport is machine-agnostic (routing is by logical inbox name; `working_dir` never leaves its host). To secure + connect:

```bash
# On the BUS machine (A): set a password + (optional) TLS certs, then start
echo "AETHER_REDIS_PASSWORD=$(openssl rand -hex 24)" >> aether/.env
aether/scripts/make-certs.sh 172.16.100.55        # IP/host of A → cert SAN; writes aether/certs/
docker compose -f aether/docker-compose.yml up -d  # TLS auto-enables on :6380

# On a CLIENT machine (B): join the bus + register this project's body
export AETHER_REDIS_PASSWORD=...                   # same secret (env, never the profile)
aether register --host 172.16.100.55 --port 6380 --tls --tls-ca /path/to/ca.crt --id my_proj
aether observatory my_proj                         # go online against the remote bus
```

`aether bus use …` persists the (non-secret) endpoint to `~/.aether/config.json` so later commands inherit it. Precedence is **flag > env > profile > default**; the **password is taken from `AETHER_REDIS_PASSWORD` only, never stored in the profile**. Each host's `constellation.yaml` should list **only its own body** (registry-as-truth; registering a conflicting id fails closed — use `--force` to override).

> **What auth + TLS does NOT give you:** a shared password means no per-sender authentication inside the trust domain (anyone with it can write to any inbox); a server cert expiry takes the whole fleet offline at once (rotate early). Redis HA / clustering is still out of scope.

For real `claude -p` e2e tests (slow, run at least once per phase):
```bash
cd aether && python3 -m pytest -q --run-e2e -m e2e
```

---

## Connect your own local projects

This wires two (or more) of *your* real projects together so their Claude agents talk directly. The triggered `claude -p` runs with **read-only tools** (Read/Glob/Grep) by default — safe for a first run with no human in the loop.

> The raw `python3 aether/<script>.py` commands below are the explicit form; the **[`aether` CLI](#the-aether-cli)** wraps them (`aether client setup`, `aether observatory <id>`, `aether send/consult …`) and is the recommended path.

**1. Register your projects** in [`aether/constellation.yaml`](aether/constellation.yaml) — one `body` per project, with `working_dir` pointing at the real folder:

```yaml
bodies:
  frontend:
    description: "Frontend & design system"
    capabilities: ["ui", "react"]
    inbox: "aether:inbox:frontend"
    working_dir: "/Users/you/code/frontend"
  backend:
    description: "Backend orders API & database"
    capabilities: ["api", "db"]
    inbox: "aether:inbox:backend"
    working_dir: "/Users/you/code/backend"
```

**2. Start Redis**, then **launch one Observatory per project** — each in its own terminal, and **keep them running** (a project must be online to receive messages):

```bash
docker compose -f aether/docker-compose.yml up -d redis
python3 aether/run_observatory.py frontend     # terminal 1
python3 aether/run_observatory.py backend      # terminal 2
```

**3. (Optional) Open Stargazer** to watch the conversation live:

```bash
python3 -m aether.stargazer.server             # → http://127.0.0.1:8765
```

**4. Kick off a conversation** from a fourth terminal:

```bash
# directed question (Comet) — backend's Claude reads its own repo and answers
python3 aether/send_message.py --to backend --from frontend \
    --intent ask --text "What JSON field is an order's unique identifier?"

# broadcast announcement (Wave) to every project — no replies expected
python3 aether/send_message.py --wave --text "Deploying v2 at 02:00 UTC."
```

The recipient's Claude reads its project, answers, and the asker concludes — the conversation converges on its own, well within Horizon. Every hop is visible in Stargazer and reconstructable from `aether:events`.

### Interactive "go consult project X"

`send_message.py` is fire-and-forget. For an interactive session (you open Claude Code inside one project and want it to consult another and bring back the answer), use `consult.py` — it sends from a transient identity, **waits**, and prints the reply:

```bash
# Only the project you are CONSULTING needs its Observatory running:
python3 aether/run_observatory.py genesis

# Then, from anywhere (or have your interactive Claude Code run this):
python3 aether/consult.py --to genesis \
    --text "Which SpecBundle fields does your BundleParser require?"
#  → prints genesis's grounded answer inline
```

To make **"go consult genesis about this detail: …"** work natively inside a project's Claude Code, add a one-line hint to that project's `CLAUDE.md` telling it to run the command above. The consulting side does **not** need its own Observatory — your interactive session *is* that side.

### The `aether` CLI

A single `aether` command unifies setup + the existing scripts.

**Recommended — install with pipx** (puts `aether` on PATH in an isolated venv; no manual clone needed):

```bash
pipx install git+https://github.com/OffskyLab/orrery-aether
aether --help
```

Or run it straight from a clone (no install): `python3 -m aether.cli <cmd>`, `python3 <abs>/aether/cli.py <cmd>`, or `aether install-shim` (writes a thin shim that points back at the clone). Note: any machine that runs a **server** (Observatory / MCP / Stargazer / operator) needs the code present — pipx or clone; only the Redis bus itself runs from the public `redis:7` image.

```bash
python3 -m aether.cli install-shim          # install a shim into ~/.local/bin (points at this clone)
aether mcp setup                            # set up the MCP server in this project (→ Claude Code "/" tools)
aether client setup                         # register this project as a Body + connect to Redis
aether server status                        # is Redis up + who is online
aether who                                  # list talkable projects
aether observatory <id>                     # launch a project's resident Observatory (= run_observatory.py)
aether send / consult ...                   # = send_message.py / consult.py
aether bus use --host <ip> --port <p> ...   # point this machine at a (remote) bus + persist the endpoint
aether register --host <ip> --port <p> ...  # join a (remote) bus + register this project's body
```

- `aether mcp setup`: detects the current project → uses a stable identity `<project>-mcp` (never collides with the Observatory id) → writes the server into `.mcp.json` with an **absolute python path** (idempotent merge, preserves existing servers). Flags: `--scope project|local|user`, `--method mcp-json|claude-cli`.
- `aether client setup`: **infers** description/capabilities from `CLAUDE.md`/manifest for you to confirm (`--yes`/flags for non-interactive) → registers **this body only** on the bus (additive, fail-closed) → pings Redis.

### Most seamless: the Aether MCP server

`aether/mcp_server.py` is an MCP server (FastMCP, stdio). Register it in a project's `.mcp.json` (easiest: `aether mcp setup`) and Claude Code gets **six `aether_*` tools** (model-invoked) **+ six `/mcp__aether__*` slash commands** (user-invoked) — **no CLAUDE.md edits**. It represents your session as a transient bus identity and runs no headless claude (your interactive session is the brain on this side); only the peer you consult needs its Observatory running.

| Tool | Purpose |
|---|---|
| `aether_list_bodies()` | who can I talk to + who's online |
| `aether_ask(to, question, thread?)` | **consultant**: ask a peer (async), threaded follow-ups |
| `aether_poll(thread)` | pick up the reply + status |
| `aether_discuss(from, to, topic)` | **autonomous**: two running Observatories hash it out |
| `aether_transcript(thread)` | rebuild a thread's full timeline |
| `aether_control(thread, action)` | **operator**: pause / resume / terminate |

**Plus "/" slash commands.** The server also exposes MCP **prompts**, which Claude Code shows in the `/` menu as `/mcp__aether__<name>` — letting you trigger Aether directly instead of waiting for the model to decide to call a tool:

| Slash command | Behaviour |
|---|---|
| `/mcp__aether__who` | list talkable projects + who's online (prefetched at render) |
| `/mcp__aether__ask <to> <question>` | have the model call `aether_ask`, then hand off to `/poll` (no send at render) |
| `/mcp__aether__poll <thread>` | fetch reply + status |
| `/mcp__aether__discuss <from> <to> <topic>` | two projects discuss autonomously; watch with `/transcript` |
| `/mcp__aether__transcript <thread>` | rebuild the full thread (bounded) |
| `/mcp__aether__stop <thread>` | confirm-first terminate of a runaway conversation |

Register it — easiest is the CLI (writes `.mcp.json` with a stable `<project>-mcp` identity and an absolute python path automatically):

```bash
aether mcp setup                            # in the project dir
#   or user scope: claude mcp add aether -e AETHER_REDIS_DB=0 -- \
#                    <abs-python> /ABS/PATH/aether/mcp_server.py --identity <project>-mcp
#   or copy aether/mcp.example.json into the project's .mcp.json
```

Then in Claude Code (opened in EventStormingTool) either type `/mcp__aether__ask genesis "…"` or just say *"ask genesis: which SpecBundle fields does your BundleParser need?"* — it calls `aether_ask`, then `aether_poll`, and reports genesis's grounded answer.

> **Safety:** keep tools read-only for a first real run; widen only deliberately with `run_observatory.py --allow-write` (gives the triggered Claude write/exec — there is no human gate inside the loop). Start with ONE real project paired with a sandbox before wiring real↔real (§13.5 "shrink the blast radius"). Each conversation is capped by the rate limit (`--rate-per-min`) and Horizon. To intervene (pause / terminate a runaway conversation), run the **operator panel** (above).

---

## Tests

The fast suite is **deterministic and runs in milliseconds** — `claude` and the clock are both injectable (`FakeClaudeRunner` + `ManualClock`), so no real CLI calls and no real waiting. `core/` never depends on the `claude` CLI.

| Suite | Count | Highlights |
|---|---|---|
| Phase 1 | 9 | convergence · Horizon (proportional scaling, off-by-one) · rate · dedup · routing · reliable delivery |
| Phase 2 | 19 | session resume · malformed fail-safe · crash idempotency (exact counts) · routing · offline hold · **injection isolation** · §17 register |
| Phase 3 | 25 | **reconstruction fidelity** · live update · extinction · constellation · telescope · **read-only invariant** · scale/reconnect |
| Phase 4 | 12 | Wave no-amplify · solicit bounded · Horizon · offline · **panel↔Stargazer isolation** · operator isolation · pause/resume/terminate · audit |
| CLI | 24 | pure helpers (merge/append/infer/escape) · dispatcher routing · mcp/client setup · `/` prompts · alias passthrough |
| Cross-machine | 15 | make_redis auth/TLS (byte-identical default) · resolver precedence (flag>env>profile) · registry CAS / additive / prune · duplicate-id fail-closed · bus-use persistence · null-working_dir guard |
| **Total** | **104 passed, 2 gated e2e** | + adversarial validation: 100+ probe cases, 0 confirmed defects |

**Non-negotiable invariants proven structurally:**
- **Stargazer is read-only** — no reachable write path to Redis/inbox/registry (`test_p3_scenario_6_readonly.py`, unchanged through Phase 4).
- **Injection isolation** — an inbound message body only ever appears inside the delimited "untrusted external message" block, never in an instruction position.
- **Operator privilege is "may initiate", not "may bypass"** — operator-injected messages are still treated as untrusted by the receiver.

---

## Repository layout

```
orrery-aether/
├── README.md            ← you are here (English front door)
├── README.zh-TW.md      ← 中文（繁體）版本
├── Aether-規劃.md       ← the full spec (v6, §0–§19) — single source of truth
└── aether/              ← the implementation
    ├── cli.py                the unified `aether` command (dispatcher)
    ├── cli_support.py        pure helpers (mcp/constellation merge, infer) — unit-tested
    ├── core/conn.py          cross-machine connection resolver + bus profile
    ├── constellation.yaml    register your projects here
    ├── run_observatory.py    launch a resident Observatory for one project
    ├── send_message.py       kick off a conversation from the CLI (fire-and-forget)
    ├── consult.py            ask one project & wait for the reply inline (interactive)
    ├── mcp_server.py         MCP server: 6 aether_* tools + 6 /mcp__aether__* prompts
    ├── mcp.example.json      copy-paste .mcp.json registration
    ├── docker-compose.yml    Redis (AOF; 6379 loopback / 6380 TLS) + Stargazer + Operator panel
    ├── scripts/make-certs.sh self-signed CA + server cert (with IP SAN) for cross-machine TLS
    ├── demo_*.py             real claude -p end-to-end demos (scenario1 / phase2 / phase4 / register)
    ├── core/            envelope, guardrails, client, processing-log, registry, heartbeat, control, conn
    ├── observatory/     the resident listener: runner, parsing, prompt, register, pipeline
    ├── stargazer/       read-only dashboard (FastAPI + SSE + single-file SPA)
    ├── operator_panel/  authenticated control plane (the only write path)
    ├── tests/           104 fast tests + 2 gated real-claude e2e
    ├── docs/            plan / discussion / spec / DEVLOG (CLI + cross-machine work)
    └── README.md        ← detailed phase-by-phase implementation & acceptance notes
```

- **Front door (this file)** — overview, vocabulary, quick start.
- **[`aether/README.md`](aether/README.md)** — detailed per-phase design, acceptance-scenario ↔ test mapping, pinned decisions.
- **[`Aether-規劃.md`](Aether-規劃.md)** — the authoritative spec; all `§` references in the code point here.

---

## Maintaining this README

**This README must be kept in sync with the code. Whenever a feature is added or changed, update this file, its Chinese translation [`README.zh-TW.md`](README.zh-TW.md), and `aether/README.md` in the same change** — phase status, vocabulary/architecture, run commands, and test counts.
