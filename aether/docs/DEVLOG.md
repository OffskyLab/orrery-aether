# Aether DEVLOG

每個邏輯變更一條：what / why / 決策依據。對應 git commit。

## 2026-06-01 — 跨機部署（Redis auth+TLS / register / 遠端訂閱）

Pipeline：`/plan`(00-plan.md) → `/discuss`(01-discussion.md, consensus 2 rounds, D1–D5) →
`/write-spec`(02-spec.md) → `/audit-spec`(內部 4b/5b + Codex 外審 2 輪，5高6中2低 + N1高/N2/N3 全修，
status ready，registry/2026-06-01-cross-machine.json)。

### Step 1–3 — 連線安全基礎 + registry additive（foundation）
- `core/aether_client.py`：`make_redis` 加 keyword-only `password/username/ssl/ssl_ca_certs/
  ssl_certfile/ssl_keyfile`；**無參數時 kwargs 僅 host/port/db/decode_responses（byte-identical）**，
  有值才帶（不誤改 AUTH/TLS）。
- `core/conn.py`（新）：`load_bus_profile`（永不 raise）+ `resolve_redis_kwargs`，precedence
  **flag>env>profile>default**；profile 不蓋 env；**密碼不從 profile 讀**；profile 預設 AUTO 自動載入。
- `core/registry.py`：`DuplicateBodyError` + **原子 `register_body`（WATCH/MULTI CAS）**；`sync` 預設
  改 **additive**（`prune=True` 保留 delete-all 給 demo seed）；`load_and_sync` 傳遞參數。
- `tests/test_cross_machine.py`（新，12 測試）：make_redis byte-identical/ssl passthrough、resolver
  precedence/密碼不入 profile/AUTO 載入/ssl tri-state、sync additive vs prune、duplicate fail-closed、
  CAS WatchError 重試。
- **Why**：F1 的單一連線入口 + F2/F3 的跨機星圖一致性地基。100 測試綠（88→100），無回歸；additive sync
  在 flushed test db 上與舊 delete-all 結果相同，故既有測試不受影響。

## 2026-05-31 — 統一 `aether` CLI

Spec: `aether/docs/tasks/2026-05-31-aether-cli.md`（status ready）。
流程：/plan → /discuss（2 輪，Codex+Gemini，共識）→ /write-spec → /audit-spec（全綠）。

### Phase 0 — 變更紀錄基建 (commit 197b5d8)
- `git init` + `.gitignore`（`__pycache__/`、`.DS_Store`、`aether-redis-data/`、local `.mcp.json`）+ baseline commit。
- **Why**: repo 原本非 git，是「記錄每次修改」最大缺口（討論 C10）。baseline 鎖住 65 測試綠的狀態，之後每變更可 bisect。

### Step 1 — cli_support.py 純函式
- `sanitize_id` / `merge_mcp_config`（含形狀驗證）/ `build_mcp_server_entry`（寫 sys.executable）/
  `append_constellation_body`（yaml.safe_dump 跳脫、append-only 保留 header）/ `infer_metadata`（不發明）。
- 10 個單元測試綠（含 codex 指出的 YAML 跳脫 + merge 形狀陷阱）。
- **Why**: 純函式抽離讓 setup 邏輯可在無 Redis/disk 下測（討論 C3/C6/C7 + audit 修正）。

### Step 2/3 — argv refactor + cli.py dispatcher
- 4 腳本 `main()` → `main(argv=None)`（向後相容，75 測試綠）。
- `aether/cli.py`：argparse dispatcher（mcp setup / client setup / server status / who / install-shim
  / observatory|send|consult alias）+ sys.path bootstrap（模組層唯一所有權）。
- mcp setup 寫 `sys.executable` 絕對路徑 + 穩定 `<project>-mcp` identity + .mcp.json 冪等 merge；
  client setup append-only 寫 constellation + redis ping + load_and_sync。
- **Why**: 統一入口（討論定案）；C1 延後 packaging，用 `-m`/絕對路徑/install-shim 達成 global 指令。

### Step 6 — 「/」MCP prompts + 心跳重註冊
- `build_server` 加 6 個 `@mcp.prompt()`：reads（who/poll/transcript）render 時 prefetch；
  writes（ask/discuss/stop）回最小指令字串、**render 不寫入**（防 double-send，C5/Codex）；transcript bounded；stop confirm-first。
- `AetherBridge.start_heartbeat` 每 tick 重 `registry.add`（修 Codex HIGH：sync 後自我修復）。
- 實機驗證：6 prompts 經真實 stdio 呈現為 `/mcp__aether__<name>`。全套 85 測試綠。

### Step 7 — 文件 + 驗收
- 兩份 README 補 `aether` CLI 用法 + 「/」slash 指令表（依 keep-readme-updated 規則）。
- Spec「Agent 必做」全綠：85 測試、abs-cwd 入口、關鍵 symbol、ask 指令-only 安全性。
- 實機：6 prompts 經真實 stdio 呈現為 `/mcp__aether__<name>`。

## 2026-06-01 — docker compose 收進 web 介面（Redis + Stargazer + 操作面板）

使用者要「web 介面跟 MQ 一起跑」。重大選擇先問：兩題確認 → **三服務常駐** + **token 自動產生到 .env**。

### What
- `stargazer/server.run` + `operator_panel/server.run`：參數改 `None` + `AETHER_*` env fallback
  （`AETHER_STARGAZER_HOST`/`AETHER_OPERATOR_HOST`/`AETHER_REDIS_HOST`/`_PORT`/`_DB`），
  **無 env/無參數時行為與舊版完全相同**（向後相容；兩函式皆 `# pragma: no cover`）。
- 新 `aether/Dockerfile`（python:3.11-slim，一份 image 兩 entrypoint，PEP 420 namespace pkg）
  + `.dockerignore`（排除 .env/tests/docs/__pycache__）。
- `docker-compose.yml`：加 `stargazer`（8765，唯讀）、`operator`（8770，特權）兩 service，
  `depends_on: redis healthy`，各帶 health check；container 內綁 `0.0.0.0`，主機埠
  **只發佈到 `127.0.0.1`** → 維持 §15.6／§18.3 localhost-only 暴露不變量。
- `aether/.env`（gitignored，`openssl rand -hex 32` token）+ `.env.example`；root `.gitignore` 加 `.env`。

### Why
- env fallback 而非改 compose command：12-factor、與既有 `AETHER_OPERATOR_TOKEN`/`AETHER_REDIS_DB`/
  `AETHER_CONSTELLATION` 慣例一致，且不破壞 `run()` 原生預設（127.0.0.1:8765 / localhost:6379）。
- 容器綁 0.0.0.0 但主機 publish 127.0.0.1：Docker 下保住「LAN 連不到」的安全前提（不可改成裸埠）。
- Redis 仍 `6379` 對全介面（沿用舊設定，不擅自縮限跨機 agent；README 註明可鎖 127.0.0.1）。

### 驗證
- 85 測試綠（run() 改動無回歸）。
- `up -d --build` → 三 container healthy；`/api/health` `{readonly:true}`、operator `/health` ok。
- 安全：operator 無 token 寫入 → **401**；帶 token → 200（pause→resume 復原，無殘留）；
  `compose config` 顯示 web 兩埠 `host_ip: 127.0.0.1`。

## 2026-06-01 — 修 `aether send/observatory/consult` alias 轉發 bug

實機驗證 CLAUDE.md 載入時發現 `aether send --to genesis …` 報 `unrecognized arguments: --to`。

### 根因
alias 子指令用 `nargs=argparse.REMAINDER` 接後續參數,但 CPython argparse 在
**remainder 第一個 token 是 option（`--to`）時會誤判**(bpo-17050),把它丟回頂層 parser → 報錯。
只有第一個轉發 token 是 positional 時才正常。`mcp serve` 同一寫法有同樣潛在 bug。

### 修法
`cli.py` 新增 `_PASSTHROUGH`（argv prefix → 模組），在 `main()` **argparse 之前**攔截
`observatory`/`send`/`consult`/`mcp serve`,把其後 argv **原樣**轉給該腳本 `main()`
(longest-prefix 優先,讓 `mcp serve` 贏過 `mcp`)。子 parser 保留供 `--help` 列示與
positional-only fallback。

### 驗證
- 新增 `test_passthrough_forwards_leading_options`(parametrized ×3:send/observatory/mcp serve)。
- 全套 **88 測試綠**(85→88);`aether send --help` 正確轉發顯示 send_message 參數。
- 實機:`aether send --to event_storming_tool-mcp --from genesis-mcp --intent inform --text …`
  → 成功投遞(以無 Observatory 的收件人,不觸發 claude),確認後清除。
- 副發現(未改,屬正常行為):回覆未登錄 body 會被擋 `invalid_recipient`(recipient 護欄)。
