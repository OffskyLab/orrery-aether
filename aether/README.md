# Aether — Phase 1 + Phase 2 + Phase 3 + Phase 4 + 統一 CLI

訊息基礎建設，讓分屬不同專案的 Claude 代理直接互相溝通。

- **Phase 1**（規格 §11/§12）：Redis（介質）＋ 信封 ＋ 三層護欄（Horizon／速率／
  去重）＋ 兩個測試用 Observatory。證明「迴圈會收斂、不會跑掉」。
- **Phase 2**（規格 §13/§14）：真實 `claude -p`、session resume、registry 路由探索、
  接真實專案的安全隔離。在 crash／畸形輸出／目標離線下**不重複付費、不重複送、不
  無限迴圈、不執行被注入的指令**，全程可由 `aether:events` 重建。
- **Phase 3 · Stargazer**（規格 §15/§16）：唯讀觀測儀表板。人能**即時看見、也能事後
  完整重建**任一對話，畫面忠實對應 `aether:events`，且**在任何情況下都無法擾動系統**
  （純唯讀，結構性證明）。四視圖：星圖／對話時間軸／即時望遠鏡／熄滅紀錄。
- **Phase 4 · Wave 廣播 ＋ 操作面板**（規格 §18/§19）：一對多公告**不引發扇出風暴**
  （預設公告不回覆／solicit 才回且回應走定向 Comet／禁止 Wave 回 Wave／扇出計入
  Horizon）；人類透過一個**獨立、需驗證（localhost+token）、可稽核**的操作面板介入
  （注入／暫停／恢復／終止），**全程不削弱 Stargazer 唯讀不變量**——§16.1-6 對抗測試原封不動仍全綠。

> 範圍外：Redis 高可用／叢集（暫緩）。**跨機部署已支援**（密碼+TLS、`aether register`、registry-as-truth；
> 規格 `docs/cross-machine/02-spec.md`）。§17 通訊語域（反客套）自 Phase 2 起全程適用。

## 結構

```
aether/
├── docker-compose.yml      # 整套：Redis（AOF；6379 loopback / 6380 TLS）+ Stargazer + 操作面板
├── scripts/make-certs.sh   # 自簽 CA + server cert（含 IP SAN）給跨機 TLS
├── Dockerfile              # web image（一份 image、兩個 entrypoint）
├── .env.example            # 複製成 .env 填 AETHER_OPERATOR_TOKEN（.env 已 gitignore）
├── constellation.yaml      # 測試 Body 星座登錄表
├── core/                   # 不依賴 claude CLI（規格 §11.2 / §14.2）
│   ├── envelope.py         # 信封：建立 / 驗證 / 序列化 + 可推導 reply id（§13.1）
│   ├── clock.py            # 可注入時間源（速率視窗 / heartbeat）
│   ├── guards.py           # Horizon / RateLimiter / Dedup
│   ├── aether_client.py    # emit + 鏡像 + consumer group 讀/ack/claim + 離線 hold 佇列
│   ├── processing_log.py   # §13.1 冪等狀態機（RECEIVED→CLAUDE_DONE→REPLY_EMITTED→ACKED）
│   ├── session_store.py    # §13.3 conversation_id → session_id 持久化
│   ├── registry.py         # §13.4 constellation 載入 / 同步 / 驗證
│   └── heartbeat.py        # §13.4 存活探索
├── observatory/
│   ├── claude_runner.py    # ClaudeRunner 介面（回傳 raw turn）+ Real(claude -p) + Fake
│   ├── control.py          # §13.2 防禦性解析 + schema 驗證（fail-safe 政策在 pipeline）
│   ├── prompt.py           # §13.5 注入隔離 + §17.2/§17.3 語域片段（concise / critical）
│   ├── register.py         # §17.1 確定性閘門：ack 不出站 + 保守空內容 lint（反客套）
│   ├── crash.py            # §14.2 崩潰注入（在指定 log 狀態模擬死亡）
│   └── main.py             # 完整管線：狀態機 + 重試 + 路由/online/hold + §17 語域閘門 + 鎖 + 重投
├── stargazer/              # Phase 3 · 唯讀觀測儀表板（不依賴、也不可寫入系統）
│   ├── readonly.py         # §16.1-6 ReadOnlyRedis：封死所有寫入命令
│   ├── events.py           # §15.2 有界近期窗口（XREVRANGE）+ 追尾（XREAD 游標）
│   ├── viewmodels.py       # §15.4 視圖純函式 + §18.2 operator_log / timeline actions
│   ├── server.py           # §15.2 FastAPI + SSE（localhost only，零寫入端點）
│   ├── live_capture.py     # §16.3 即時看一場對話從發起到自然收斂（frame 序列）
│   └── web/index.html      # 單檔 SPA（星圖 SVG + 時間軸 + 望遠鏡 + 熄滅紀錄 + operator）
├── operator_panel/         # Phase 4 · 操作面板（獨立、需驗證的寫入路徑；絕不在 Stargazer）
│   ├── control_service.py  # §18.2 注入/暫停/恢復/終止 + 稽核（operator_action）
│   └── server.py           # §18.3 FastAPI，localhost+token，未驗證寫入→401
├── cli.py                  # 統一 `aether` CLI（mcp setup / client setup / server status / who / install-shim / alias）
├── cli_support.py          # 純函式：merge_mcp_config / append_constellation_body / infer_metadata（可單元測試）
├── run_observatory.py      # 啟動單一真實專案的常駐 Observatory（接你的本地專案用）
├── send_message.py         # CLI：發起一場跨專案對話（Comet 或 Wave，含稽核，發了就走）
├── consult.py              # CLI：問一個專案並「同步等待」取回答案（互動用，一次性身分）
├── mcp_server.py           # MCP server（FastMCP/stdio）：6 個 aether_* 工具 + 6 個 /mcp__aether__* prompts
├── mcp.example.json        # .mcp.json 註冊範本
├── tests/                  # P1 + P2 + P3 + P4 情境 + CLI（85 快測，e2e 以 --run-e2e 閘控）
├── docs/                   # CLI 的 plan / discussion / spec / DEVLOG（變更紀錄）
├── demo_scenario1.py       # Phase 1 真實 claude e2e demo
├── demo_phase2.py          # Phase 2 真實 claude e2e demo（多跳 + 路由選擇 + resume）
├── demo_phase4.py          # Phase 4 demo（Wave + operator 暫停→恢復→終止，時間軸可見）
└── demo_register.py        # §17 反客套 demo（真實 Observatory 擋下 ack 回覆）
```

## 接你自己的本地專案

用統一 CLI（推薦）：在專案目錄 `aether client setup`（登錄 Body + 連 Redis）→
`aether observatory <id>`（上線收訊）→ `aether mcp setup`（讓該專案 Claude Code 有「/」工具）。
原始腳本等價形式：`python3 aether/run_observatory.py <id>`、`python3 aether/send_message.py
--to <id> --text "..."`、`python3 aether/consult.py --to <id> --text "..."`（同步等回覆）。
被觸發的 `claude -p` 預設**唯讀工具**（Read/Glob/Grep），要放寬才加 `--allow-write`。
詳見根目錄 README 的「Connect your own local projects」與「The `aether` CLI」。

`core/control.py` 的控制狀態（pause/kill）放 Redis key，Observatory 只**讀**、操作面板才
**寫**——依賴方向乾淨（observatory→core；operator_panel→core），Stargazer 完全不碰。

三層護欄與路由的核心邏輯全在 `core/`，與「呼叫 claude 的外殼」徹底解耦：測試以
`FakeClaudeRunner`（腳本化回應）＋ `ManualClock`（手動推進）執行，不呼叫真實
`claude -p`、不等待真實時鐘，毫秒級可重複。

## 跑起來

```bash
# 1. 啟動 Redis（只要介質）
docker compose -f aether/docker-compose.yml up -d redis

# 1-all. 或一次跑起整套（Redis + Stargazer + 操作面板）
cp aether/.env.example aether/.env && sed -i '' "s/change-me/$(openssl rand -hex 32)/" aether/.env
docker compose -f aether/docker-compose.yml up -d --build
#   Stargazer → http://127.0.0.1:8765（唯讀）   操作面板 → http://127.0.0.1:8770（需 .env token）

# 1-x. 跨機（A=匯流排，B/C 連入）：A 設密碼+TLS、B 用 register 加入
#   A: echo "AETHER_REDIS_PASSWORD=$(openssl rand -hex 24)" >> aether/.env
#      aether/scripts/make-certs.sh <A的IP> && docker compose -f aether/docker-compose.yml up -d
#   B: export AETHER_REDIS_PASSWORD=...   # 同密碼（走 env，不入 profile）
#      aether register --host <A的IP> --port 6380 --tls --tls-ca ca.crt --id <proj>
#      aether observatory <proj>          # 對遠端 bus 上線
#   參數優先序 flag>env>profile>default；bus use 把非密碼端點存 ~/.aether/config.json。

# 2. 安裝相依
python3 -m pip install -r aether/requirements.txt

# 3. 快速確定性測試（全部 Phase + CLI，可進 CI，85 passed；e2e 自動跳過）
cd aether && python3 -m pytest -q

# 3b. 統一 CLI（免 pip）：裝 shim 後 bare 指令可用
python3 -m aether.cli install-shim
aether mcp setup        # 在某專案目錄：設好 MCP（→ Claude Code「/」工具）
aether client setup     # 登錄為 Body 並連 Redis

# 4. 真實 claude e2e 測試（閘控，跑一次）
python3 -m pytest -v --run-e2e -m e2e

# 5. 真實 claude e2e demo
python3 demo_scenario1.py   # Phase 1
python3 demo_phase2.py      # Phase 2（多跳 + 路由選擇 + resume）

# 6. Stargazer 儀表板（唯讀，僅綁 localhost）
python3 -m aether.stargazer.server          # 開 http://127.0.0.1:8765
python3 aether/stargazer/live_capture.py    # 即時看一場對話收斂（frame 序列）
```

## Phase 1 驗收情境 ↔ 測試（規格 §11.1）

| # | 情境 | 測試檔 |
|---|------|--------|
| 1 | 正常收斂 | `tests/test_scenario_1_convergence.py` |
| 2 | Horizon 強制觸發（含等比例縮放 / off-by-one） | `tests/test_scenario_2_horizon.py` |
| 3 | 速率限制（含視窗重置，用可注入時鐘） | `tests/test_scenario_3_rate_limit.py` |
| 4 | 去重 | `tests/test_scenario_4_dedup.py` |
| 5 | 路由正確 | `tests/test_scenario_5_routing.py` |
| 6 | 可靠投遞（當機前未 ACK → 重啟重投） | `tests/test_scenario_6_reliable_delivery.py` |

## Phase 2 驗收情境 ↔ 測試（規格 §14.1）

| # | 情境 | 測試檔 | 類型 |
|---|------|--------|------|
| 1 | 多跳 session resume | `tests/test_p2_scenario_1_resume_e2e.py` | e2e（真實） |
| 2 | 畸形輸出 fail-safe（含有界重試） | `tests/test_p2_scenario_2_malformed.py` | 快速 |
| 3 | 崩潰不重複付費／不重複送 | `tests/test_p2_scenario_3_crash_idempotency.py` | 快速 |
| 4 | 路由：Claude 選收件者（含無效拒發） | `tests/test_p2_scenario_4_routing.py` | 快速 |
| 5 | 離線目標 hold | `tests/test_p2_scenario_5_offline.py` | 快速 |
| 6 | 注入隔離（結構性 + 行為性） | `tests/test_p2_scenario_6_injection_isolation.py` | 快速 |
| 7 | 正常端對端（路由＋session＋收斂） | `tests/test_p2_scenario_7_full_e2e.py` | e2e（真實） |
| 8 | **通訊語域（§17 反客套／反附和）** | `tests/test_p2_scenario_8_register.py` | 快速 |

### §17 通訊語域（反客套與反附和 · Phase 2 起全程適用）

- **§17.1 確定性閘門（硬規則）**：`body.intent == "ack"` 的回覆**一律不出站**（只放行
  ask/inform/task/result）；可選的**保守空內容 lint** 把純社交文字降級——寧可放過也
  不誤殺實質內容。兩者皆記 `reason=ack_suppressed`（gate=`intent_ack` / `empty_content_lint`）。
  「沉默即確認」：不回覆＝已收到並理解。
- **§17.2 語域 prompt（強力軟控制）**：注入「對方是工程服務不是人、不問候/道謝/稱讚/
  確認/複述、像簡潔 API、沒有推進任務的內容就 `reply_needed=false`」＋ reply_needed 門檻。
- **§17.3 反附和**：依對話關係（`register_policy(from,to) → concise|critical`）掛上
  批判語域；critical 明確要求對抗性立場、不為和諧而附和。
- **§17.4/§17.5 誠實限度**：硬閘門對「要不要發」近滴水不漏（確定性測試精確斷言）；
  prompt 對「內容長怎樣」只能降低不能保證（§17.5-4 批判立場屬 e2e/受控評估，
  本suite以結構性測試證明語域片段確實掛上並送達 prompt，**不因測不準而放寬硬規則**）。
- Stargazer 熄滅紀錄／reason 詞彙已認得 `ack_suppressed`。

## Phase 3 驗收情境 ↔ 測試（規格 §16.1）

| # | 情境 | 測試檔 |
|---|------|--------|
| 1 | 重建忠實性（render == 事件流，逐跳一致） | `tests/test_p3_scenario_1_fidelity.py` |
| 2 | 即時更新（append → 依序收到） | `tests/test_p3_scenario_2_live_update.py` |
| 3 | 熄滅可見（horizon/rate/dedup/malformed/offline 五種） | `tests/test_p3_scenario_3_extinction.py` |
| 4 | 離線星體（heartbeat 過期變暗 / 恢復活躍） | `tests/test_p3_scenario_4_constellation.py` |
| 5 | 即時望遠鏡（里程碑依序、回合結束即停） | `tests/test_p3_scenario_5_telescope.py` |
| 6 | **唯讀不變量（結構性證明無任何寫入路徑）** | `tests/test_p3_scenario_6_readonly.py` |
| 7 | 規模與重連（有界載入 + 重連不重複） | `tests/test_p3_scenario_7_scale_reconnect.py` |
| — | 冒煙（頁面載入 + 連上 SSE） | `tests/test_p3_smoke.py` |

## Phase 4 驗收情境 ↔ 測試（規格 §19.1）

| # | 情境 | 測試檔 |
|---|------|--------|
| 1 | Wave 公告不放大（N body 各處理一次、0 自動回覆） | `tests/test_p4_wave.py` |
| 2 | 徵求回覆有界且不再廣播（定向 Comet 回原發起者） | `tests/test_p4_wave.py` |
| 3 | Wave 扇出計入 Horizon | `tests/test_p4_wave.py` |
| 4 | 離線 body 重連後仍收到 Wave | `tests/test_p4_wave.py` |
| 5 | **面板↔Stargazer 隔離 + 寫入需驗證（§16.1-6 回歸閘門）** | `tests/test_p4_operator.py` |
| 6 | 操作員注入仍受接收端隔離（§14.1-6） | `tests/test_p4_operator.py` |
| 7 | 暫停／恢復／終止 生效且時間軸可見 | `tests/test_p4_operator.py` |
| 8 | 稽核完整（actor + ts，可由 aether:events 重建） | `tests/test_p4_operator.py` |

### §18.3 拍板決定（你已確認）

1. **面板驗證** → localhost + bearer token；未驗證寫入 → 401（遠端再加 TLS／真實驗證）。
2. **操作集** → 注入 + 暫停／恢復／終止 + 稽核（手動回覆與調參延後）。
3. **Wave 定址** → 先全體廣播（`to="broadcast"`）；capability 群播列為可選。

### Phase 4 跑起來

```bash
# 操作面板（獨立服務，localhost + token）
AETHER_OPERATOR_TOKEN=secret python3 -m aether.operator_panel.server   # http://127.0.0.1:8770
# Phase 4 demo（Wave + operator 暫停→恢復→終止，動作在時間軸可見）
python3 aether/demo_phase4.py
```

> **回歸閘門（不可協商）**：`tests/test_p3_scenario_6_readonly.py` 在 Phase 4 後**原封不動**
> 仍全綠（檔案 sha 未變、Stargazer 路由仍 GET/HEAD only、寫入命令全被封）。操作面板是
> **另一個** app，寫入需 token，未驗證被拒。

## 統一 CLI（`aether` …）↔ 測試

流程：`/plan → /discuss（2 輪，Codex+Gemini 共識）→ /write-spec → /audit-spec（全綠）→ 實作`。
spec：`docs/tasks/2026-05-31-aether-cli.md`；討論：`docs/discussions/2026-05-31-aether-cli.md`；
變更紀錄：`git init` + 每階段一 commit + `docs/DEVLOG.md`。

| 主題 | 測試檔 |
|---|---|
| 純函式（merge_mcp_config 冪等＋形狀驗證、append_constellation_body 保留 header＋YAML 跳脫、infer_metadata 不發明） | `tests/test_cli_support.py`（10） |
| dispatcher 路由、`mcp setup` 寫穩定 `<project>-mcp`＋絕對 python＋冪等 merge、`client setup` append、install-shim | `tests/test_cli.py`（6） |
| 「/」prompts：reads render 時 prefetch；writes（ask/discuss/stop）**render 不送訊息/不 terminate**（防 double-send） | `tests/test_mcp_prompts.py`（4） |

設計定案（討論 C1–C10）：延後 pip packaging（用 `-m`/絕對路徑/`install-shim`）；MCP 預設 project scope；
`.mcp.json` 冪等 merge 為主、`claude mcp add` 為輔；identity `<project>-mcp` 與 Body `<project>` 刻意不同；
prompts reads-prefetch / writes-instruction-only；constellation append-only（PyYAML，不加 ruamel）；
`server` 只做 `status`；兩個 setup 獨立（不加 `aether init`）。

## §15.6 拍板決定（你已確認）

1. **即時望遠鏡** → 只轉發里程碑（turn_start／tool_use／turn_done），完整逐字串流
   放在 `ProgressForwarder(verbatim=True)` 開關後面（預設關）。
2. **事件保留** → `aether:events` 以 `XADD MAXLEN ~50000` 近似裁剪；初次載入只讀有界
   近期窗口（`recent(window)`，預設 200），非全歷史。
3. **暴露範圍** → Stargazer 僅綁 `127.0.0.1`（`server.run` 預設），不對外。Docker 跑時，
   容器內綁 `0.0.0.0`（`AETHER_STARGAZER_HOST`／`AETHER_OPERATOR_HOST`），但 compose 的
   主機埠只發佈到 `127.0.0.1`，所以 LAN 仍連不到——同一條 localhost-only 不變量。

## §13.6 拍板決定（你已確認）

1. **離線目標** → hold 排隊，目標回線再投（`aether:hold:<target>` + `flush_hold`）。
2. **畸形輸出** → 1 次有界重試（請只重輸出 JSON），仍失敗則 fail-safe（不回覆、記
   `reason=malformed_output`）。
3. **真實專案權限** → 唯讀＋產出建議，不授予不可逆動作（prompt 安全規則明定；
   Observatory 永不執行任何由 body 衍生的動作，見情境 6 結構性不變量）。

## 護欄設計要點

- **Horizon**（layer 1，硬天花板）：收訊息時檢查 `hop_count >= max_hops` 即熄滅。
  情境 2 證明停止跳數與 `max_hops` 等比例（2/4/8 → 各停在 2/4/8），抓 off-by-one。
- **Claude 自決回覆**（layer 2，軟控制）：預設不回，除非結構化輸出 `reply_needed`。
- **速率限制**（layer 3）：以可注入時鐘的固定視窗計數，超量記 `reason=rate_limited`。
- **去重**：成功處理後才標記（crash-safe）；重複投遞同一 `message_id` 只生效一次，
  當機未完成的訊息會被重投而非遺失。
