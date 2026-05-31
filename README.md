# Aether

> **Message infrastructure that lets Claude Code agents in different projects talk directly to each other — without a human relaying between them.**
> **讓分屬不同專案的 Claude Code 代理之間，不經人類轉述、直接互相溝通的訊息基礎建設。**

The naming follows a cosmology metaphor: **Aether is the medium (spacetime itself)** through which messages travel.
命名沿用宇宙論的隱喻：**Aether 是介質（時空本身）**，訊息在其中傳遞。

---

## What is this? / 這是什麼？

**EN** — Aether is a Redis-Streams-based message bus for autonomous agent-to-agent communication. Each project runs a resident listener (an **Observatory**) that receives messages, turns them into a prompt, calls `claude -p`, and decides whether to reply. The whole system is built so that **it can never run away**: conversations either converge naturally or are stopped by hard guardrails, and every message is reconstructable from a single event stream.

**中** — Aether 是一套以 Redis Streams 為基礎的訊息匯流排，讓代理之間自主溝通。每個專案跑一個常駐 listener（**Observatory／天文台**）：收訊息 → 加工成 prompt → 呼叫 `claude -p` → 決定要不要回覆。整個系統的設計核心是**「絕不失控」**：對話要嘛自然收斂，要嘛被硬護欄擋下，且每一則訊息都能從單一事件流完整重建。

### Why "it can never run away" matters / 為什麼「絕不失控」是核心

Two agents replying to each other could loop forever — burning tokens and money. Aether's **Horizon** (`hop_count` ceiling) is the answer to Olbers' paradox applied to messaging: like why the night sky isn't infinitely bright, a signal can't travel infinitely far — the echo dies out.
兩個代理可能無限互相回覆、把成本燒光。Aether 的 **Horizon（視界，`hop_count` 上限）** 就像奧伯斯佯謬的答案——訊號傳不了無限遠、會自然熄滅，讓回音停下來。

---

## Vocabulary / 命名詞彙表

| Component / 元件 | Name / 名稱 | Cosmology / 宇宙論意象 |
|---|---|---|
| The whole system / MQ medium · 整個系統／介質層 | **Aether** | Vacuum / spacetime — the medium that lets causal contact happen · 真空、時空 |
| A project · 一個專案 | **Body**（星體） | A self-gravitating celestial body with its own history · 自我引力束縛的天體 |
| Directed message · 定向訊息 | **Comet**（彗星） | A discrete object on a fixed orbit toward one body · 沿確定軌道飛向特定天體 |
| Broadcast message · 廣播訊息 | **Wave**（重力波） | An event rippling outward; whoever has a detector receives it · 向外輻射的擾動 |
| Resident listener · 常駐 listener | **Observatory**（天文台） | A station with detectors, always catching ripples · 隨時捕捉漣漪的觀測站 |
| Hop limit (loop guard) · 跳數上限 | **Horizon**（視界） | The boundary that lets signals die out naturally · 讓訊號自然熄滅的邊界 |
| Project registry · 專案登錄表 | **Constellation**（星座圖） | A star chart of known bodies · 已知天體的星表 |
| Read-only dashboard · 視覺化儀表板 | **Stargazer**（觀星者） | Lets humans see the whole sky at once · 讓人類看見整片天空 |

---

## Architecture / 架構總覽

```
                         ┌─────────────────────────┐
                         │   Stargazer (read-only)  │  ← humans watch the sky here
                         │   reads aether:events    │     人類在這裡看整片天空（唯讀）
                         └────────────▲────────────┘
                                      │ SSE
                                      │            ┌──────────────────────┐
   ┌─────────────┐          ┌─────────┴────────┐   │ Operator Panel       │ ← the only write path
   │  Body: A    │          │     Aether       │   │ (separate, authed)   │   唯一的寫入路徑
   │ Observatory │◀────────▶│  (Redis Streams) │◀──│ inject/pause/        │   （獨立、需驗證）
   │  + claude -p│  XADD /  │  inbox:A         │   │ resume/terminate     │
   └─────────────┘ XREADGRP │  inbox:B         │   └──────────────────────┘
   ┌─────────────┐          │  broadcast (Wave)│
   │  Body: B    │◀────────▶│  events (mirror) │
   │ Observatory │          │  registry (hash) │
   └─────────────┘          └──────────────────┘
```

- **Aether (Redis)** — the medium. Per-project inbox streams, a broadcast stream, a global event mirror, and the registry. · 介質：每專案收件匣、廣播串流、全域事件鏡像、星座登錄表。
- **Observatory** — one resident process per project; does four dumb things (receive → build prompt → call `claude -p` → maybe reply) plus enforces the guardrails. · 每專案一個常駐程序，只做四件笨事＋執行護欄。
- **Stargazer** — a **read-only** web dashboard. Pure observer; it can never perturb the system. · **唯讀**儀表板，純觀測者，永不擾動系統。
- **Operator Panel** — a **separate, authenticated** service: the only write path for human intervention. · **獨立、需驗證**的服務：人工介入的唯一寫入路徑。

---

## The four phases / 四個階段

| Phase | What / 內容 | Status |
|---|---|---|
| **1 · Minimal & won't run away** · 最小可跑且不會跑掉 | Redis + envelope + three guardrails (Horizon / rate-limit / dedup) + two test Observatories · 信封＋三層護欄＋兩個測試 Observatory | ✅ |
| **2 · Real claude / session / routing** · 真實 claude／session／路由 | Real `claude -p`, multi-hop session resume, idempotency log, defensive output parsing, registry routing, heartbeat, injection isolation · 多跳 resume／冪等日誌／防禦性解析／路由探索／注入隔離 | ✅ |
| **3 · Stargazer** · 觀測儀表板 | Read-only dashboard: star map / conversation timeline / live telescope / extinction log; faithful reconstruction from `aether:events` · 星圖／時間軸／即時望遠鏡／熄滅紀錄 | ✅ |
| **4 · Wave + Operator Panel** · 廣播＋操作面板 | One-to-many announcements without fan-out explosion + authenticated human intervention (inject/pause/resume/terminate), all audited · 廣播防爆＋需驗證的人工介入＋稽核 | ✅ |

> **§17 Communication register / 通訊語域** (anti-pleasantry, anti-sycophancy) applies from Phase 2 onward: a pure `ack`/thank-you reply never leaves the station (`reply_needed=false`, logged as `ack_suppressed`) — silence means "received and understood".
> **§17 通訊語域**（反客套、反附和）自 Phase 2 起全程適用：純 `ack`／道謝回覆一律不出站，沉默即確認。

> **Out of scope / 範圍外:** Redis high-availability and cross-machine deployment (deferred).

---

## Quick start / 快速開始

Requirements / 需求：Docker, Python 3.10+, and the `claude` CLI (only needed for the real end-to-end demos; the fast test suite mocks it).

```bash
# 1. Start Redis (the medium) / 啟動 Redis（介質）
docker compose -f aether/docker-compose.yml up -d redis

# 2. Install deps / 安裝相依
python3 -m pip install -r aether/requirements.txt

# 3. Run the fast, deterministic test suite (CI-able; real-claude e2e is gated)
#    跑快速確定性測試（可進 CI；真實 claude e2e 另以閘控）
cd aether && python3 -m pytest -q          # 65 passed
cd ..

# 4. Real claude -p end-to-end demos / 真實 claude 端對端 demo
python3 aether/demo_scenario1.py           # Phase 1: A asks → B answers → converges
python3 aether/demo_phase2.py              # Phase 2: multi-hop + routing choice + session resume
python3 aether/demo_phase4.py              # Phase 4: Wave + operator pause→resume→terminate

# 5. Stargazer dashboard (read-only, localhost only) / 唯讀儀表板（僅 localhost）
python3 -m aether.stargazer.server         # → http://127.0.0.1:8765

# 6. Operator panel (separate, authenticated) / 操作面板（獨立、需驗證）
AETHER_OPERATOR_TOKEN=secret python3 -m aether.operator_panel.server   # → http://127.0.0.1:8770
```

For real `claude -p` e2e tests (slow, run at least once per phase): / 真實 claude e2e 測試（慢、每階段至少跑一次）：
```bash
cd aether && python3 -m pytest -q --run-e2e -m e2e
```

---

## Connect your own local projects / 接上你自己的本地專案

This wires two (or more) of *your* real projects together so their Claude agents
talk directly. The triggered `claude -p` runs with **read-only tools**
(Read/Glob/Grep) by default — safe for a first run with no human in the loop.
讓你自己的兩個（以上）真實專案互通，代理直接對話。被觸發的 `claude -p` 預設只給
**唯讀工具**（Read/Glob/Grep），第一次跑很安全。

**1. Register your projects** in [`aether/constellation.yaml`](aether/constellation.yaml) —
one `body` per project, with `working_dir` pointing at the real folder:
**1. 在 `aether/constellation.yaml` 登錄你的專案**（每個專案一個 `body`，`working_dir` 指向真實資料夾）：
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

**2. Start Redis**, then **launch one Observatory per project** — each in its own
terminal, and **keep them running** (a project must be online to receive messages):
**2. 啟動 Redis，再為每個專案各開一個終端機跑 Observatory，並保持運行**（專案要在線才收得到訊息）：
```bash
docker compose -f aether/docker-compose.yml up -d redis
python3 aether/run_observatory.py frontend     # terminal 1
python3 aether/run_observatory.py backend      # terminal 2
```

**3. (Optional) Open Stargazer** to watch the conversation live:
**3.（選用）開 Stargazer 即時觀看：**
```bash
python3 -m aether.stargazer.server             # → http://127.0.0.1:8765
```

**4. Kick off a conversation** from a fourth terminal:
**4. 從第四個終端機發起對話：**
```bash
# directed question (Comet) — backend's Claude reads its own repo and answers
python3 aether/send_message.py --to backend --from frontend \
    --intent ask --text "What JSON field is an order's unique identifier?"

# broadcast announcement (Wave) to every project — no replies expected
python3 aether/send_message.py --wave --text "Deploying v2 at 02:00 UTC."
```

The recipient's Claude reads its project, answers, and the asker concludes — the
conversation converges on its own, well within Horizon. Every hop is visible in
Stargazer and reconstructable from `aether:events`.
收件方的 Claude 會讀自己的專案、回答，發問方收到後自然收斂。每一跳都在 Stargazer 可見、可由 `aether:events` 重建。

### Interactive "go consult project X" / 在某專案內直接「跟另一個專案討論」

`send_message.py` is fire-and-forget. For an interactive session (you open Claude
Code inside one project and want it to consult another and bring back the answer),
use `consult.py` — it sends from a transient identity, **waits**, and prints the reply:
`send_message.py` 是發了就走；若你在某專案內開著 Claude Code、想叫它去問另一個專案並把答案帶回來，用 `consult.py`——它用一次性身分發問、**等待**、印出回覆：

```bash
# Only the project you are CONSULTING needs its Observatory running:
# 只有「被諮詢」的那個專案需要先跑 Observatory：
python3 aether/run_observatory.py genesis

# Then, from anywhere (or have your interactive Claude Code run this):
# 然後，從任何地方執行（或讓你互動中的 Claude Code 去跑）：
python3 aether/consult.py --to genesis \
    --text "Which SpecBundle fields does your BundleParser require?"
#  → prints genesis's grounded answer inline / 直接印出 genesis 讀完自己 repo 的答案
```

To make **"請跟 genesis 討論這個細節：…"** work natively inside a project's Claude
Code, add a one-line hint to that project's `CLAUDE.md` telling it to run the
command above. The consulting side does **not** need its own Observatory — your
interactive session *is* that side.
要讓 **「請跟 genesis 討論這個細節：…」** 在某專案的 Claude Code 內自然生效，只要在該專案的
`CLAUDE.md` 加一行提示叫它執行上面的指令即可。發問方**不需要**自己的 Observatory——你互動中的 session 就是發問方。

### The `aether` CLI / 統一 CLI

A single `aether` command unifies setup + the existing scripts. Three ways to run
it (no pip install needed): `python3 -m aether.cli <cmd>`, `python3 <abs>/aether/cli.py <cmd>`,
or install a bare `aether` shim once with `aether install-shim`.
一個 `aether` 指令統一設定與既有腳本。三種跑法（免 pip 安裝）：`python3 -m aether.cli <cmd>`、
`python3 <abs>/aether/cli.py <cmd>`，或先 `install-shim` 裝一支 `aether` shim 到 PATH。

```bash
python3 -m aether.cli install-shim          # 裝 `aether` 到 ~/.local/bin（一次）
aether mcp setup                            # 在當前專案設定 MCP server（→ Claude Code 的「/」工具）
aether client setup                         # 把當前專案登錄成 Body 並連到 Redis
aether server status                        # Redis 是否在線 + 誰 online
aether who                                  # 列出可對話的專案
aether observatory <id>                     # 啟動某專案的常駐 Observatory（= run_observatory.py）
aether send / consult ...                   # = send_message.py / consult.py
```

- `aether mcp setup`：偵測當前專案 → 用穩定 identity `<project>-mcp`（與 Observatory id 不撞）→
  把 server 以**絕對 python 路徑**寫進 `.mcp.json`（冪等 merge，保留既有 server）。`--scope project|local|user`、
  `--method mcp-json|claude-cli`。
- `aether client setup`：從 `CLAUDE.md`/manifest **推斷** description/capabilities 讓你確認（`--yes`/旗標可非互動）→
  **append-only** 寫進 `constellation.yaml`（逐字保留 header 註解）→ ping Redis。

### Most seamless: the Aether MCP server / 最無縫：Aether MCP server

`aether/mcp_server.py` is an MCP server (FastMCP, stdio). Register it in a project's
`.mcp.json` and Claude Code auto-discovers six `aether_*` tools — **no CLAUDE.md edits,
no manual CLI**. It represents your session as a transient bus identity and runs no
headless claude (your interactive session is the brain on this side); only the peer
you consult needs its Observatory running.
把 `aether/mcp_server.py` 註冊進專案的 `.mcp.json`，Claude Code 就自動有六個 `aether_*` 工具——
**不用改 CLAUDE.md、不用手敲指令**。它把你的 session 當成一次性的 bus 身分，這邊不跑 headless
claude（你的互動 session 就是大腦）；只有被諮詢的對方需要開 Observatory。

| Tool | Purpose / 用途 |
|---|---|
| `aether_list_bodies()` | who can I talk to + who's online / 有誰可談、誰在線 |
| `aether_ask(to, question, thread?)` | **consultant**: ask a peer (async), threaded follow-ups / 諮詢：問一個專案、可接續 |
| `aether_poll(thread)` | pick up the reply + status / 取回覆與狀態 |
| `aether_discuss(from, to, topic)` | **autonomous**: two running Observatories hash it out / 讓兩個專案自己討論 |
| `aether_transcript(thread)` | rebuild a thread's full timeline / 重建整段對話 |
| `aether_control(thread, action)` | **operator**: pause / resume / terminate / 暫停、恢復、終止 |

**Plus "/" slash commands.** The server also exposes MCP **prompts**, which Claude Code
shows in the `/` menu as `/mcp__aether__<name>` — letting you trigger Aether directly
instead of waiting for the model to decide to call a tool:
**外加「/」斜線指令**：server 同時暴露 MCP **prompts**，在 Claude Code 的 `/` 選單以
`/mcp__aether__<name>` 出現，讓你直接叫用：

| Slash command | 行為 |
|---|---|
| `/mcp__aether__who` | 直接列出可對話的專案 + 誰 online（render 時 prefetch） |
| `/mcp__aether__ask <to> <question>` | 叫模型用 `aether_ask` 問、交棒 `/poll`（不在 render 送訊息） |
| `/mcp__aether__poll <thread>` | 取回覆 + 狀態 |
| `/mcp__aether__discuss <from> <to> <topic>` | 兩專案自主討論，配 `/transcript` 觀看 |
| `/mcp__aether__transcript <thread>` | 重建整段對話（bounded） |
| `/mcp__aether__stop <thread>` | confirm-first 終止失控對話 |

Register it — easiest is the CLI (writes `.mcp.json` with a stable `<project>-mcp` identity
and an absolute python path automatically):
註冊——最簡單用 CLI（自動寫穩定 identity + 絕對 python 路徑）：

```bash
aether mcp setup                            # in the project dir / 在專案目錄
#   or user scope: claude mcp add aether -e AETHER_REDIS_DB=0 -- \
#                    <abs-python> /ABS/PATH/aether/mcp_server.py --identity <project>-mcp
#   or copy aether/mcp.example.json into the project's .mcp.json
```

Then in Claude Code (opened in EventStormingTool) either type `/mcp__aether__ask genesis "…"`
or just say *"問 genesis：你的 BundleParser 需要哪些 SpecBundle 欄位？"* — it calls `aether_ask`,
then `aether_poll`, and reports genesis's grounded answer.

> **Safety / 安全:** keep tools read-only for a first real run; widen only deliberately
> with `run_observatory.py --allow-write` (gives the triggered Claude write/exec — there is
> no human gate inside the loop). Start with ONE real project paired with a sandbox before
> wiring real↔real (§13.5 "shrink the blast radius"). Each conversation is capped by the
> rate limit (`--rate-per-min`) and Horizon. To intervene (pause / terminate a runaway
> conversation), run the **operator panel** (below).
> **安全：** 第一次跑保持唯讀；要放寬才用 `--allow-write`（會給寫入／執行權，迴圈中沒有人把關）。
> 先用一個真實專案配一個沙箱，再真實對真實。每段對話受速率限制與 Horizon 封頂；要介入
> （暫停／終止失控對話）就開下面的**操作面板**。

---

## Tests / 測試

The fast suite is **deterministic and runs in milliseconds** — `claude` and the clock are both injectable (`FakeClaudeRunner` + `ManualClock`), so no real CLI calls and no real waiting. `core/` never depends on the `claude` CLI.
快速測試**確定性、毫秒級**——`claude` 與時鐘皆可注入，不呼叫真實 CLI、不等真實時鐘；`core/` 不依賴 claude CLI。

| Suite | Count | Highlights |
|---|---|---|
| Phase 1 | 9 | convergence · Horizon (proportional scaling, off-by-one) · rate · dedup · routing · reliable delivery |
| Phase 2 | 19 | session resume · malformed fail-safe · crash idempotency (exact counts) · routing · offline hold · **injection isolation** · §17 register |
| Phase 3 | 25 | **reconstruction fidelity** · live update · extinction · constellation · telescope · **read-only invariant** · scale/reconnect |
| Phase 4 | 12 | Wave no-amplify · solicit bounded · Horizon · offline · **panel↔Stargazer isolation** · operator isolation · pause/resume/terminate · audit |
| CLI | 20 | pure helpers (merge/append/infer/escape) · dispatcher routing · mcp/client setup · `/` prompts (reads prefetch, writes no render-side-effect) |
| **Total** | **85 passed, 2 gated e2e** | + adversarial validation: 100+ probe cases, 0 confirmed defects |

**Non-negotiable invariants proven structurally / 以結構性測試證明的不可協商不變量:**
- **Stargazer is read-only** — no reachable write path to Redis/inbox/registry (`test_p3_scenario_6_readonly.py`, unchanged through Phase 4).
- **Injection isolation** — an inbound message body only ever appears inside the delimited "untrusted external message" block, never in an instruction position.
- **Operator privilege is "may initiate", not "may bypass"** — operator-injected messages are still treated as untrusted by the receiver.

---

## Repository layout / 專案結構

```
orrery-aether/
├── README.md            ← you are here / 你在這裡（中英 front door）
├── Aether-規劃.md       ← the full spec (v6, §0–§19) / 完整規格（單一真相來源）
└── aether/              ← the implementation / 實作
    ├── cli.py                the unified `aether` command (dispatcher) / 統一 CLI 入口
    ├── cli_support.py        pure helpers (mcp/constellation merge, infer) — unit-tested / 純函式（可測）
    ├── constellation.yaml   register your projects here / 在此登錄你的專案
    ├── run_observatory.py    launch a resident Observatory for one project / 啟動單一專案的 Observatory
    ├── send_message.py       kick off a conversation from the CLI (fire-and-forget) / 從 CLI 發起對話
    ├── consult.py            ask one project & wait for the reply inline (interactive) / 問一個專案並同步取回答案
    ├── mcp_server.py         MCP server: 6 aether_* tools for Claude Code (no CLAUDE.md edits) / 給 Claude Code 的 MCP 工具
    ├── mcp.example.json      copy-paste .mcp.json registration / .mcp.json 註冊範本
    ├── core/            envelope, guardrails, client, processing-log, registry, heartbeat, control
    ├── observatory/     the resident listener: runner, parsing, prompt, register, pipeline
    ├── stargazer/       read-only dashboard (FastAPI + SSE + single-file SPA)
    ├── operator_panel/  authenticated control plane (the only write path)
    ├── tests/           65 fast tests + 2 gated real-claude e2e
    └── README.md        ← detailed phase-by-phase implementation & acceptance notes / 逐階段細節
```

- **Front door (this file)** — overview, vocabulary, quick start. · 總覽、詞彙、快速開始。
- **[`aether/README.md`](aether/README.md)** — detailed per-phase design, acceptance-scenario ↔ test mapping, pinned decisions. · 逐階段設計、驗收情境對應、拍板決定。
- **[`Aether-規劃.md`](Aether-規劃.md)** — the authoritative spec; all `§` references in the code point here. · 權威規格；程式中所有 `§` 指向此。

---

## Maintaining this README / 維護本 README

**This README must be kept in sync with the code. Whenever a feature is added or changed, update this README (and `aether/README.md`) in the same change** — phase status, the vocabulary/architecture, run commands, and test counts.
**本 README 必須與程式碼保持同步。每當新增或變更功能時，請在同一次改動中一併更新本 README（與 `aether/README.md`）**——包含階段狀態、詞彙／架構、執行指令與測試數量。
