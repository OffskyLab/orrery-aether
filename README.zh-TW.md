# Aether

[English](README.md) · [**中文（繁體）**](README.zh-TW.md)

> **讓分屬不同專案的 Claude Code 代理之間，不經人類轉述、直接互相溝通的訊息基礎建設。**

命名沿用宇宙論的隱喻：**Aether 是介質（時空本身）**，訊息在其中傳遞。

---

## 這是什麼？

Aether 是一套以 Redis Streams 為基礎的訊息匯流排，讓代理之間自主溝通。每個專案跑一個常駐 listener（**Observatory／天文台**）：收訊息 → 加工成 prompt → 呼叫 `claude -p` → 決定要不要回覆。整個系統的設計核心是**「絕不失控」**：對話要嘛自然收斂，要嘛被硬護欄擋下，且每一則訊息都能從單一事件流完整重建。

### 為什麼「絕不失控」是核心

兩個代理可能無限互相回覆、把成本燒光。Aether 的 **Horizon（視界，`hop_count` 上限）** 就像奧伯斯佯謬的答案——訊號傳不了無限遠、會自然熄滅，讓回音停下來。

---

## 命名詞彙表

| 元件 | 名稱 | 宇宙論意象 |
|---|---|---|
| 整個系統／介質層 | **Aether** | 真空、時空——讓因果接觸發生的介質 |
| 一個專案 | **Body**（星體） | 自我引力束縛、有自身歷史的天體 |
| 定向訊息 | **Comet**（彗星） | 沿確定軌道飛向特定天體的離散物體 |
| 廣播訊息 | **Wave**（重力波） | 向外輻射的擾動，有偵測器就收得到 |
| 常駐 listener | **Observatory**（天文台） | 隨時捕捉漣漪的觀測站 |
| 跳數上限（迴圈護欄） | **Horizon**（視界） | 讓訊號自然熄滅的邊界 |
| 專案登錄表 | **Constellation**（星座圖） | 已知天體的星表 |
| 唯讀儀表板 | **Stargazer**（觀星者） | 讓人類一眼看見整片天空 |

---

## 架構總覽

```
                         ┌─────────────────────────┐
                         │   Stargazer (read-only)  │  ← 人類在這裡看整片天空（唯讀）
                         │   reads aether:events    │
                         └────────────▲────────────┘
                                      │ SSE
                                      │            ┌──────────────────────┐
   ┌─────────────┐          ┌─────────┴────────┐   │ Operator Panel       │ ← 唯一的寫入路徑
   │  Body: A    │          │     Aether       │   │ (獨立、需驗證)        │
   │ Observatory │◀────────▶│  (Redis Streams) │◀──│ inject/pause/        │
   │  + claude -p│  XADD /  │  inbox:A         │   │ resume/terminate     │
   └─────────────┘ XREADGRP │  inbox:B         │   └──────────────────────┘
   ┌─────────────┐          │  broadcast (Wave)│
   │  Body: B    │◀────────▶│  events (mirror) │
   │ Observatory │          │  registry (hash) │
   └─────────────┘          └──────────────────┘
```

- **Aether（Redis）**——介質：每專案收件匣、廣播串流、全域事件鏡像、星座登錄表。
- **Observatory**——每專案一個常駐程序，只做四件笨事（收 → 組 prompt → 呼叫 `claude -p` → 決定要不要回）＋執行護欄。
- **Stargazer**——**唯讀**儀表板，純觀測者，永不擾動系統。
- **Operator Panel**——**獨立、需驗證**的服務：人工介入的唯一寫入路徑。

---

## 四個階段

| 階段 | 內容 | 狀態 |
|---|---|---|
| **1 · 最小可跑且不會跑掉** | Redis ＋ 信封 ＋ 三層護欄（Horizon／速率／去重）＋ 兩個測試 Observatory | ✅ |
| **2 · 真實 claude／session／路由** | 真實 `claude -p`、多跳 session resume、冪等日誌、防禦性輸出解析、路由探索、heartbeat、注入隔離 | ✅ |
| **3 · Stargazer** | 唯讀儀表板：星圖／對話時間軸／即時望遠鏡／熄滅紀錄；由 `aether:events` 忠實重建 | ✅ |
| **4 · Wave ＋ 操作面板** | 一對多廣播防爆 ＋ 需驗證的人工介入（注入／暫停／恢復／終止），全程稽核 | ✅ |

> **§17 通訊語域**（反客套、反附和）自 Phase 2 起全程適用：純 `ack`／道謝回覆一律不出站（`reply_needed=false`，記為 `ack_suppressed`）——沉默即「收到並理解」。

> **範圍外：** Redis 高可用／叢集（暫緩）。跨機部署（密碼 ＋ TLS ＋ `aether register`）**已支援**——見 [跨機部署](#跨機部署hub-and-spoke--密碼--tls)。

---

## 快速開始

需求：Docker、Python 3.10+、`claude` CLI（只有真實端對端 demo 才需要；快速測試會 mock 掉）。

```bash
# 1. 啟動 Redis（介質）
docker compose -f aether/docker-compose.yml up -d redis

# 2. 安裝相依
python3 -m pip install -r aether/requirements.txt

# 3. 跑快速確定性測試（可進 CI；真實 claude e2e 另以閘控）
cd aether && python3 -m pytest -q          # 104 passed
cd ..

# 4. 真實 claude -p 端對端 demo
python3 aether/demo_scenario1.py           # Phase 1：A 問 → B 答 → 收斂
python3 aether/demo_phase2.py              # Phase 2：多跳 ＋ 路由選擇 ＋ session resume
python3 aether/demo_phase4.py              # Phase 4：Wave ＋ operator 暫停→恢復→終止

# 5. Stargazer 儀表板（唯讀，僅綁 localhost）
python3 -m aether.stargazer.server         # → http://127.0.0.1:8765

# 6. 操作面板（獨立、需驗證）
AETHER_OPERATOR_TOKEN=secret python3 -m aether.operator_panel.server   # → http://127.0.0.1:8770
```

### 用 Docker 一次跑起整套

```bash
# 設定一次 operator token（已 gitignore）
cp aether/.env.example aether/.env && sed -i '' "s/change-me/$(openssl rand -hex 32)/" aether/.env

# 一鍵建置 web image 並同時啟動 Redis + Stargazer + 操作面板
docker compose -f aether/docker-compose.yml up -d --build
#   Stargazer  → http://127.0.0.1:8765   （唯讀）
#   Operator   → http://127.0.0.1:8770   （需 aether/.env 內的 token）
docker compose -f aether/docker-compose.yml down      # 全部停止
```

三個服務都在容器裡跑。web app 在容器內綁 `0.0.0.0`，但每個主機埠**只發佈到 `127.0.0.1`**，所以只能本機連、**LAN 連不到**——和原生跑時一樣的 localhost-only 暴露（§15.6／§18.3）。操作面板從 gitignored 的 `aether/.env` 讀 `AETHER_OPERATOR_TOKEN`。Redis 的明文埠 `6379` 現在**只綁 loopback**；TLS 埠 `6380` 才是跨機匯流排（見下）。

### 跨機部署（hub-and-spoke ＋ 密碼 ＋ TLS）

A 機器跑 Redis 當匯流排；B/C 各跑自己的 Observatory 指向 A。傳輸與機器無關（路由用邏輯收件匣名，`working_dir` 不離開本機）。安全連線：

```bash
# 在 BUS 機器（A）：設密碼 ＋（選用）TLS 憑證，再啟動
echo "AETHER_REDIS_PASSWORD=$(openssl rand -hex 24)" >> aether/.env
aether/scripts/make-certs.sh 172.16.100.55        # A 的 IP/host → 進 cert SAN；寫到 aether/certs/
docker compose -f aether/docker-compose.yml up -d  # TLS 自動在 :6380 開

# 在 CLIENT 機器（B）：加入匯流排 ＋ 註冊本專案的 body
export AETHER_REDIS_PASSWORD=...                   # 同一組密碼（走 env，絕不寫進 profile）
aether register --host 172.16.100.55 --port 6380 --tls --tls-ca /path/to/ca.crt --id my_proj
aether observatory my_proj                         # 對遠端 bus 上線
```

`aether bus use …` 會把**非密碼**端點存到 `~/.aether/config.json`，之後的指令自動繼承。參數優先序 **flag > env > profile > default**；**密碼只從 `AETHER_REDIS_PASSWORD` 讀、絕不寫進 profile**。每台的 `constellation.yaml` 應**只列自己的 body**（registry-as-truth；同 id 衝突會 fail-closed，要覆寫用 `--force`）。

> **密碼 ＋ TLS 不會給你的東西：** 共享密碼 = 信任域內**無寄件者認證**（拿到密碼的人都能對任何收件匣寫）；server 憑證到期會讓全網同時斷線（提早輪替）。Redis 高可用／叢集仍不在範圍。

真實 `claude -p` e2e 測試（慢、每階段至少跑一次）：
```bash
cd aether && python3 -m pytest -q --run-e2e -m e2e
```

---

## 接上你自己的本地專案

讓你自己的兩個（以上）真實專案互通，代理直接對話。被觸發的 `claude -p` 預設只給**唯讀工具**（Read/Glob/Grep），第一次跑很安全。

> 下面用原始 `python3 aether/<script>.py` 最直白；**[`aether` CLI](#aether-cli)** 把它們包起來（`aether client setup`／`aether observatory <id>`／`aether send/consult …`），是推薦做法。

**1. 在 [`aether/constellation.yaml`](aether/constellation.yaml) 登錄你的專案**（每個專案一個 `body`，`working_dir` 指向真實資料夾）：

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

**2. 啟動 Redis，再為每個專案各開一個終端機跑 Observatory，並保持運行**（專案要在線才收得到訊息）：

```bash
docker compose -f aether/docker-compose.yml up -d redis
python3 aether/run_observatory.py frontend     # 終端機 1
python3 aether/run_observatory.py backend      # 終端機 2
```

**3.（選用）開 Stargazer 即時觀看：**

```bash
python3 -m aether.stargazer.server             # → http://127.0.0.1:8765
```

**4. 從第四個終端機發起對話：**

```bash
# 定向問題（Comet）——backend 的 Claude 讀自己的 repo 後回答
python3 aether/send_message.py --to backend --from frontend \
    --intent ask --text "What JSON field is an order's unique identifier?"

# 廣播公告（Wave）給每個專案——不預期回覆
python3 aether/send_message.py --wave --text "Deploying v2 at 02:00 UTC."
```

收件方的 Claude 會讀自己的專案、回答，發問方收到後自然收斂——在 Horizon 內自行停下。每一跳都在 Stargazer 可見、可由 `aether:events` 重建。

### 在某專案內直接「跟另一個專案討論」

`send_message.py` 是發了就走；若你在某專案內開著 Claude Code、想叫它去問另一個專案並把答案帶回來，用 `consult.py`——它用一次性身分發問、**等待**、印出回覆：

```bash
# 只有「被諮詢」的那個專案需要先跑 Observatory：
python3 aether/run_observatory.py genesis

# 然後，從任何地方執行（或讓你互動中的 Claude Code 去跑）：
python3 aether/consult.py --to genesis \
    --text "Which SpecBundle fields does your BundleParser require?"
#  → 直接印出 genesis 讀完自己 repo 的答案
```

要讓 **「請跟 genesis 討論這個細節：…」** 在某專案的 Claude Code 內自然生效，只要在該專案的 `CLAUDE.md` 加一行提示叫它執行上面的指令即可。發問方**不需要**自己的 Observatory——你互動中的 session 就是發問方。

### `aether` CLI

一個 `aether` 指令統一設定與既有腳本。三種跑法（免 pip 安裝）：`python3 -m aether.cli <cmd>`、`python3 <abs>/aether/cli.py <cmd>`，或先 `aether install-shim` 裝一支 `aether` shim 到 PATH。

```bash
python3 -m aether.cli install-shim          # 裝 `aether` 到 ~/.local/bin（一次）
aether mcp setup                            # 在當前專案設定 MCP server（→ Claude Code 的「/」工具）
aether client setup                         # 把當前專案登錄成 Body 並連到 Redis
aether server status                        # Redis 是否在線 ＋ 誰 online
aether who                                  # 列出可對話的專案
aether observatory <id>                     # 啟動某專案的常駐 Observatory（= run_observatory.py）
aether send / consult ...                   # = send_message.py / consult.py
aether bus use --host <ip> --port <p> ...   # 指向（遠端）匯流排並持久化端點
aether register --host <ip> --port <p> ...  # 加入（遠端）匯流排並註冊本專案的 body
```

- `aether mcp setup`：偵測當前專案 → 用穩定 identity `<project>-mcp`（與 Observatory id 不撞）→ 把 server 以**絕對 python 路徑**寫進 `.mcp.json`（冪等 merge，保留既有 server）。旗標：`--scope project|local|user`、`--method mcp-json|claude-cli`。
- `aether client setup`：從 `CLAUDE.md`/manifest **推斷** description/capabilities 讓你確認（`--yes`/旗標可非互動）→ 只註冊**自己這個 body**（additive、fail-closed）→ ping Redis。

### 最無縫：Aether MCP server

`aether/mcp_server.py` 是一個 MCP server（FastMCP、stdio）。把它註冊進專案的 `.mcp.json`（最簡單：`aether mcp setup`），Claude Code 就有**六個 `aether_*` 工具**（模型自呼）**＋六個 `/mcp__aether__*` 斜線指令**（使用者叫用）——**不用改 CLAUDE.md**。它把你的 session 當成一次性 bus 身分，這邊不跑 headless claude（你互動中的 session 就是這側的大腦）；只有被諮詢的對方需要開 Observatory。

| 工具 | 用途 |
|---|---|
| `aether_list_bodies()` | 有誰可談、誰在線 |
| `aether_ask(to, question, thread?)` | **諮詢**：問一個專案（非同步）、可接續 |
| `aether_poll(thread)` | 取回覆與狀態 |
| `aether_discuss(from, to, topic)` | **自主**：讓兩個在跑的 Observatory 自己討論 |
| `aether_transcript(thread)` | 重建整段對話時間軸 |
| `aether_control(thread, action)` | **operator**：暫停／恢復／終止 |

**外加「/」斜線指令**：server 同時暴露 MCP **prompts**，在 Claude Code 的 `/` 選單以 `/mcp__aether__<name>` 出現，讓你直接叫用，而不必等模型自己決定呼叫工具：

| 斜線指令 | 行為 |
|---|---|
| `/mcp__aether__who` | 直接列出可對話的專案 ＋ 誰 online（render 時 prefetch） |
| `/mcp__aether__ask <to> <question>` | 叫模型用 `aether_ask` 問、交棒 `/poll`（render 不送訊息） |
| `/mcp__aether__poll <thread>` | 取回覆 ＋ 狀態 |
| `/mcp__aether__discuss <from> <to> <topic>` | 兩專案自主討論，配 `/transcript` 觀看 |
| `/mcp__aether__transcript <thread>` | 重建整段對話（bounded） |
| `/mcp__aether__stop <thread>` | confirm-first 終止失控對話 |

註冊——最簡單用 CLI（自動寫穩定 identity ＋ 絕對 python 路徑）：

```bash
aether mcp setup                            # 在專案目錄
#   或 user scope：claude mcp add aether -e AETHER_REDIS_DB=0 -- \
#                    <abs-python> /ABS/PATH/aether/mcp_server.py --identity <project>-mcp
#   或把 aether/mcp.example.json 複製進專案的 .mcp.json
```

然後在 Claude Code（於 EventStormingTool 開啟）裡，打 `/mcp__aether__ask genesis "…"` 或直接說「問 genesis：你的 BundleParser 需要哪些 SpecBundle 欄位？」——它會呼叫 `aether_ask`，再 `aether_poll`，並回報 genesis 讀完自己 repo 的答案。

> **安全：** 第一次跑保持唯讀；要放寬才用 `run_observatory.py --allow-write`（會給被觸發的 claude 寫入／執行權，迴圈中沒有人把關）。先用一個真實專案配一個沙箱，再真實對真實（§13.5「縮小爆炸半徑」）。每段對話受速率限制（`--rate-per-min`）與 Horizon 封頂。要介入（暫停／終止失控對話），就開上面的**操作面板**。

---

## 測試

快速測試**確定性、毫秒級**——`claude` 與時鐘皆可注入（`FakeClaudeRunner` ＋ `ManualClock`），不呼叫真實 CLI、不等真實時鐘；`core/` 不依賴 claude CLI。

| 測試組 | 數量 | 重點 |
|---|---|---|
| Phase 1 | 9 | 收斂 · Horizon（比例縮放、off-by-one）· 速率 · 去重 · 路由 · 可靠投遞 |
| Phase 2 | 19 | session resume · malformed fail-safe · crash 冪等（精確計數）· 路由 · 離線 hold · **注入隔離** · §17 register |
| Phase 3 | 25 | **重建保真度** · 即時更新 · 熄滅 · constellation · 望遠鏡 · **唯讀不變量** · 規模／重連 |
| Phase 4 | 12 | Wave 不放大 · solicit bounded · Horizon · 離線 · **面板↔Stargazer 隔離** · operator 隔離 · 暫停/恢復/終止 · 稽核 |
| CLI | 24 | 純函式（merge/append/infer/escape）· dispatcher 路由 · mcp/client setup · 「/」prompts · alias 轉發 |
| 跨機 | 15 | make_redis 密碼/TLS（無參數 byte-identical）· resolver 優先序（flag>env>profile）· registry CAS／additive／prune · duplicate-id fail-closed · bus-use 持久化 · null-working_dir 守門 |
| **合計** | **104 passed，2 閘控 e2e** | ＋ 對抗式驗證：100+ probe cases，0 confirmed defects |

**以結構性測試證明的不可協商不變量：**
- **Stargazer 唯讀**——對 Redis/inbox/registry 無可達寫入路徑（`test_p3_scenario_6_readonly.py`，Phase 4 後原封未動）。
- **注入隔離**——進來的訊息內容只會出現在分隔的「不可信外部訊息」區塊內，永不落在指令位置。
- **Operator 特權是「可發起」而非「可繞過」**——operator 注入的訊息在接收端仍被當成不可信。

---

## 專案結構

```
orrery-aether/
├── README.md            ← 英文 front door
├── README.zh-TW.md      ← 你在這裡（中文繁體）
├── Aether-規劃.md       ← 完整規格（v6, §0–§19）——單一真相來源
└── aether/              ← 實作
    ├── cli.py                統一 `aether` 指令（dispatcher）
    ├── cli_support.py        純函式（mcp/constellation merge、infer）——可單元測試
    ├── core/conn.py          跨機連線 resolver ＋ bus profile
    ├── constellation.yaml    在此登錄你的專案
    ├── run_observatory.py    啟動單一專案的常駐 Observatory
    ├── send_message.py       從 CLI 發起對話（發了就走）
    ├── consult.py            問一個專案並同步取回答案（互動）
    ├── mcp_server.py         MCP server：6 個 aether_* 工具 ＋ 6 個 /mcp__aether__* prompts
    ├── mcp.example.json      .mcp.json 註冊範本
    ├── docker-compose.yml    Redis（AOF；6379 loopback／6380 TLS）＋ Stargazer ＋ 操作面板
    ├── scripts/make-certs.sh 自簽 CA ＋ server cert（含 IP SAN）給跨機 TLS
    ├── demo_*.py             真實 claude -p 端對端 demo（scenario1／phase2／phase4／register）
    ├── core/            envelope、護欄、client、processing-log、registry、heartbeat、control、conn
    ├── observatory/     常駐 listener：runner、解析、prompt、register、pipeline
    ├── stargazer/       唯讀儀表板（FastAPI ＋ SSE ＋ 單檔 SPA）
    ├── operator_panel/  需驗證的控制面（唯一寫入路徑）
    ├── tests/           104 快速測試 ＋ 2 閘控真實 claude e2e
    ├── docs/            plan／discussion／spec／DEVLOG（CLI ＋ 跨機工作）
    └── README.md        ← 逐階段實作與驗收細節
```

- **Front door（本檔的英文版）**——總覽、詞彙、快速開始。
- **[`aether/README.md`](aether/README.md)**——逐階段設計、驗收情境 ↔ 測試對應、拍板決定。
- **[`Aether-規劃.md`](Aether-規劃.md)**——權威規格；程式中所有 `§` 指向此。

---

## 維護本 README

**本 README 必須與程式碼保持同步。每當新增或變更功能時，請在同一次改動中一併更新本檔、其英文版 [`README.md`](README.md) 與 `aether/README.md`**——包含階段狀態、詞彙／架構、執行指令與測試數量。
