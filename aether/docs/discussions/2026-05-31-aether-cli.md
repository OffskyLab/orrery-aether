---
topic: "Aether 統一 CLI 設計（aether mcp setup / client setup / 「/」叫用 MCP）"
status: consensus
created: "2026-05-31"
updated: "2026-05-31"
participants:
  - Claude (Opus 4.8)
  - Codex (GPT-5.4)
  - Gemini
facilitator: Claude
rounds_completed: 2
mode: default
premises:
  fixed:        # AI 不可挑戰
    - "三個使用者需求固定：aether mcp setup、aether client setup、Claude Code 用「/」叫用 MCP 能力。"
    - "「/」叫用靠 MCP prompts，呈現為 /mcp__aether__<prompt>（已驗證）；tools 由模型自呼。"
    - "SDK 的 mcp.server.fastmcp.FastMCP 同時支援 @tool 與 @prompt（已驗證）。"
    - "MCP tools 是 async（aether_ask 回 thread；aether_poll 取回覆）。"
    - "<project>-mcp identity 慣例與 Observatory Body id <project> 刻意不同，避免 inbox 衝突。"
    - "不得破壞現有 65 個通過的測試與 Phase 1–4 既有系統。"
  proposed:     # AI 可 stress-test
    - "現在就 packaging（pyproject + __init__）vs 先 ship python3 -m aether.cli。"
    - "MCP scope 預設 project vs user。"
    - ".mcp.json merge vs claude mcp add。"
    - "prompts orchestrate（render 時抓 live data）vs pure template。"
    - "client setup metadata 推斷尺度（互動詢問 vs 從 CLAUDE.md/manifest 推斷）。"
    - "constellation.yaml 註解保留策略。"
    - "server up/down 是否納入 CLI。"
meta_note: |
  種子計畫：aether/docs/cli/00-plan.md（Plan agent 產出，已 grounded）。
  目標是把這次討論的最終定案餵給 /write-spec。最多三輪。
---

# Aether 統一 CLI 設計

## 議題定義

### 背景
Aether（Redis Streams 跨專案 agent 通訊，Phase 1–4 完成、65 測試綠）目前用一堆分散
腳本（run_observatory.py / send_message.py / consult.py / mcp_server.py / stargazer /
operator_panel）。使用者要一個統一的 `aether` CLI，並能在 Claude Code 用「/」直接叫用。

### 目標
1. `aether mcp setup`：在當前專案設定 MCP server，使其能力（含「/」prompts）出現在 Claude Code。
2. `aether client setup`：把專案設成 bus 上的 client/Body 並連到 server（Redis）。
3. Claude Code 用「/」叫用 MCP 能力（靠 MCP prompts）。

### 範圍
內：CLI 架構、兩個 setup 指令、「/」prompts、變更紀錄機制、測試策略。
外：重寫既有 Phase 1–4 邏輯；Redis HA／跨機。

### 子議題（中性編號，不含 ranking）
- [ ] C1：`aether` 如何成為可執行指令 — 現在 packaging vs 先 `python3 -m aether.cli`。
- [ ] C2：MCP scope 預設 — project（committed、需核准）vs user（免核准、不進 repo）。
- [ ] C3：MCP 註冊機制 — `.mcp.json` merge vs `claude mcp add` — 支援哪個／預設哪個。
- [ ] C4：identity 標準化 — `<project>-mcp`（outbound）vs Body `<project>`（inbound）刻意不同，確認。
- [ ] C5：「/」prompts 設計 — orchestrate（render 抓 live data）vs pure template；逐 prompt 決定。
- [ ] C6：`client setup` metadata — 互動詢問 vs 推斷 description/capabilities（推斷尺度）。
- [ ] C7：`constellation.yaml` 註解保留 — PyYAML 丟註解 vs append-only vs ruamel.yaml。
- [ ] C8：`client setup`／`mcp setup` 是否互相 imply／要不要 `aether init`。
- [ ] C9：`server up/down`（shell out docker compose）是否納入，或只 `status`。
- [ ] C10：變更紀錄機制 — git init + per-change commit + DEVLOG.md。

---

## 討論紀錄

### Round 1

#### Claude (Opus 4.8)

**C1 packaging** — 立場：**現在就做最小 `pyproject.toml` + `console_scripts: aether=aether.cli:main`，`pip install -e .`**。理由（evidence）：使用者要從**任一專案目錄**打 `aether mcp setup`；`python3 -m aether.cli` 只在 repo root（或 PYTHONPATH 含 root）可用，達不到 UX。零安裝 fallback：`python3 <abs>/aether/cli.py`。風險：與 `pytest.ini` `pythonpath=..` 及四處 `sys.path.insert` 互動，需驗證 65 測試不變。

**C2 scope** — 立場：**預設 project scope**。理由：identity 必須 per-project（`<project>-mcp`），user scope 共用單一 config 不合 per-project identity 模型；project scope committed、可分享，接受一次性核准。提供 `--scope user|local`。

**C3 merge vs claude mcp add** — 立場：**預設 `.mcp.json` merge（冪等、無外部相依、deterministic），`--method claude-cli` 時走 `claude mcp add`（user scope 較順）**。evidence：`aether/mcp.example.json` 已是 merge 目標形狀。

**C4 identity** — 立場：**標準化、刻意不同**（fixed premise）。`mcp setup`→`<project>-mcp`（outbound 互動 session）；`client setup`→Body `<project>`（inbound）。evidence：`mcp.example.json` `_comment` 明示避免 inbox 衝突。

**C5 prompts** — 立場：**hybrid**。即時/唯讀（`who`、`transcript`）render 時直接抓 live data 注入（最「直接」）；async（`ask`、`discuss`）注入 ask→poll→report 指令（prompts 不能 await）。

**C6 client setup metadata** — 立場：**推斷後確認，永不靜默**。讀 `CLAUDE.md`/`README.md` 首段 + 從 manifest 偵測語言（package.json→react/ts、Package.swift→swift、pyproject→python）當「建議值」讓使用者確認；提供 `--description/--capabilities` 旗標跳過互動（非互動環境）。

**C7 YAML 註解** — 立場：**用 `ruamel.yaml` round-trip 保留註解**（`constellation.yaml` 有價值的 header 註解區塊）。代價：多一個相依。替代：PyYAML + 自寫 header 模板拼接（無新相依但較脆）。傾向 ruamel。

**C8 imply / init** — 立場：**兩者獨立**（identity 不同、職責不同：outbound 能力 vs inbound 可達），各自結尾印一行 nudge 指向對方；之後可加 `aether init` 便利指令同時跑兩者。

**C9 server up/down** — 立場：**納入 `server up|down|status`**。它是唯一 state-changing 的 docker compose wrapper，但「連到 server」本就隱含管理 server；命名明確即可。

**C10 變更紀錄** — 立場：**`git init` + 每個邏輯變更一個 commit + `DEVLOG.md`（設計理由敘事）**。evidence：repo 目前非 git，是「記錄每次修改」最大缺口。

#### Codex (GPT-5.4)

- **C1**：同意 Claude（現在 packaging）；evidence：`pytest.ini:2-4` 靠 `pythonpath=..`，各 script 自帶 `sys.path.insert`（mcp_server.py:32 等），`-m` 從別的 cwd 不可靠。
- **C2**：同意 project scope（identity per-project，user scope 單一 config 不合）。
- **C3**：同意預設 `.mcp.json` merge（須 JSON AST merge、保留其他 server 與 `_comment`）；`claude mcp add` 只當 opt-in，不可當 project 預設（會變 opaque side effect）。
- **C4**：同意（fixed）；`AetherBridge.__init__:77-79` 註冊 transient body，必非 resident Body。
- **C5**：比 Claude 更保守——render-time 只能讀，**`ask` 絕不可在 render 時送訊息**（`bridge.ask:119-123` 會 emit envelope，prompt retry 會 double-send）；`transcript` 預設掃 2000 events 全文（mcp_server.py:164-173）會爆 context，要 bounded 或注入指令；`control/stop` 傾向注入 tool call 而非 render-time terminate。
- **C6**：同意 infer-then-confirm，永不 silent；非互動補 `--description/--capability/--yes`。
- **C7**：同意 ruamel（PyYAML writer 不可接受、append-only 對 update/remove 脆）。
- **C8**：同意獨立、之後可 `aether init`。
- **C9**：**不同意 Claude**；MVP 只做 `server status`，`up/down` 不預設納入（compose 只有 Redis，CLI 變 process manager 是 deployment coupling；且從別 cwd `-f aether/docker-compose.yml` 會失效）。
- **C10**：同意，但 test gates 要綁每階段。
- **Risk Claude 漏的**：`.mcp.json` 寫 bare `aether`/`python3` 時 Claude Code 的 PATH/venv 不一定有該 executable → 應寫**絕對 python executable**；prompt render-time write/control 是最大隱性風險（duplicate bus events）。

#### Gemini

- **C1**：**不同意 Claude**——先 ship `python3 -m aether.cli`，真正 MVP 是**一行 shim 上 PATH**，不是 `pip install -e`。理由：`aether/` 無 `__init__.py`（namespace package），`pip install -e` 會引入與 `pytest.ini pythonpath=..` + 8 處 `sys.path.insert` **競爭的 .pth 機制**，同時改動 65 測試的 import 解析（高 blast radius）；且 plan 自己把 packaging 排到 Phase 5——Claude 的 C1 立場與它要捍衛的 plan 矛盾。global 指令**不需要** packaging，shim execs `python3 /abs/aether/cli.py` 即可，且 `mcp setup` 寫的是絕對路徑。
- **C2**：同意 project 被 identity 模型強制，但**互動預設翻成 `local`**（避免把 machine-specific 絕對路徑 commit 進共享 repo）。
- **C3**：同意 `.mcp.json` merge；`mcp.example.json` 整檔就是 merge 目標，pure `merge_mcp_config(existing,generated)` 零相依可測；tests 刻意 fake claude，別在 setup path 重新依賴 binary。
- **C4**：同意；**`mcp setup` 必須寫穩定 `<cwd-basename>-mcp`，不可 fall through 到 random `mcp-<uuid>`（mcp_server.py:238）**，否則 thread/inbox 名稱會 churn。
- **C5**：**部分不同意 hybrid**——使用者「/」=「更直接」的直覺是對的：reads（who/transcript/poll）一律 render-time prefetch；`ask` 同步送（它立即回 thread，bridge.ask:113-130）再交棒 `/poll`，把 async 拆成**兩個直接的「/」動作**；**絕不注入 ask→poll→report agentic loop**（那正是使用者嫌「不直接」的模型自呼體驗）。`discuss` 同理「started; watch /transcript X」。
- **C6**：同意 infer-then-confirm，並收緊：inference 品質 bimodal（EST CLAUDE.md 20K vs Genesis 1.4K），capabilities 影響**全 bus 路由**（registry.py docstring:5-7），故須當 editable draft、找不到來源就空白別發明；manifest→語言偵測安全可靠。
- **C7**：**不同意 ruamel**——用 **append-only PyYAML**（已是相依）：保留 header 逐字、只 append 新 body 區塊、永不重寫既有行 → 全部保留；ruamel 是「一次性 per-project 寫入」用最重相依，違反專案 minimalism（compose 只 Redis、tests fake claude）。
- **C8**：同意獨立，**且不同意加 `aether init`**（更多認知負荷、糊掉 outbound/inbound 切分）；用 nudge + help 文案解決。DX trap：只跑 `mcp setup` 而沒 Observatory/sync → `/who` 空 → 須印警告叫跑 `client setup` 或啟 Observatory 來 populate star chart。
- **C9**：**不同意 Claude**——MVP 只 `server status`；compose 只有 Redis，真正要 online 的「server 側」是 per-project Observatory（不在 compose），`server up` 只起 Redis 會誤導；`up/down` 是唯一 state-changing/docker-dependent，要 explicit、echo 命令、docker-gated。
- **C10**：強同意；`.git` 不存在、root 有散落 `.DS_Store`；Phase 0 先 `git init` + `.gitignore`（`__pycache__/`、`.DS_Store`、local-scope `.mcp.json`）。
- **Cross-cutting Gemini 補充**：三需求的「最簡解」比 plan 小——req1=merge `.mcp.json`、req2=append Body+ping、req3=~5 個 thin prompt 包現成同步 bridge method，**都不需 packaging/ruamel/server up-down**；argv refactor 唯一風險是 `cli.py` 必須先 `sys.path.insert(parent)` 再 import 各 main；`bridge.poll` 用 in-memory `_returned` dedup，server 重啟（`/mcp` reconnect）會 reset → `/poll` 可能重現舊回覆（known limitation）；`control terminate` 單鍵殺對話，建議 confirm-first 而非 fire-on-render。

### Round 2 — Synthesis (facilitator: Claude)

**Rejection log（HARD）— 拒絕採納 + 一句話理由：**
- ❌ Codex C7「用 ruamel.yaml」：拒絕。Gemini 的 append-only PyYAML 對「新增 body」這個常見情形可逐字保留 header、零新相依；ruamel 是一次性 per-project 寫入用最重相依，違反專案 minimalism。
- ❌ Codex + Claude C1「現在就 pip install -e packaging」：拒絕。Gemini 的 blast-radius 論點決定性——namespace package 加 editable install 會與 `pytest.ini pythonpath=..` + 8 處 `sys.path.insert` 形成競爭 .pth、同時動到 65 測試 import 解析；global 指令用 shim 即可達成，且 plan 自己排 packaging 到 Phase 5。

**部分採納（modify）：**
- 🟡 Claude C5「async 注入 ask→poll→report 指令」：修改。採 Codex「render 不可寫」+ Gemini「不要 agentic loop」——writes 注入**最小單一 tool-call 指令 + 交棒 /poll**，reads render-time prefetch。
- 🟡 Claude C9「納入 server up/down」：修改為 **MVP 只 `status`**（Codex+Gemini：compose 只 Redis、真正要 online 的是 Observatory、up/down 是唯一 state-changing）。up/down 延後、explicit、docker-gated、echo 命令。
- 🟡 Claude C1「package now」：修改為**延後 packaging**；MVP = `python3 -m aether.cli`（dev）+ 絕對路徑 `python3 <abs>/aether/cli.py`（任一 cwd）+ 可選 `aether` shim 上 PATH。pyproject + console_scripts 列 Phase 5。
- 🟡 Gemini C2「互動預設翻 local」：部分採納。使用者本 session 已明確選 project scope，故**預設仍 project**；但採 Gemini 的「絕對路徑會 commit 進 repo」警告 + 提供 `--scope local|user`。

**最終定案（餵 write-spec）：**
- **C1**：延後 packaging。`aether/cli.py` 自帶 `sys.path.insert(parent)` bootstrap（複用既有 pattern），可由 `python3 <abs>/aether/cli.py` 從任一 cwd 執行；`mcp setup` 另可裝一個薄 `aether` shim 到 PATH。65 測試在 argv refactor 後須重跑全綠。
- **C2**：預設 `--scope project`（合使用者前選 + 可分享），警告「絕對路徑會進 repo」，提供 `--scope local`（gitignore）`/user`。
- **C3**：預設 `.mcp.json` 純函式 dict-merge（冪等、保留其他 server 與 `_comment`）；`--method claude-cli` 走 `claude mcp add`（user scope 用，預設不測）。
- **C4**：`mcp setup` 寫**穩定 `<cwd-basename>-mcp`**（不可 fall through 到 random uuid）；Body=`<project>` 刻意不同。
- **C5**：reads（`who`/`transcript`/`poll`）render-time 呼叫現成同步 `AetherBridge` method、prefetch 回傳（`transcript` payload 須 bounded/截斷）；writes（`ask`/`discuss`）注入**最小單一 tool-call 指令**（call aether_ask → 回 thread → 交棒 `/poll`；**不注入 poll 迴圈、render 時不送訊息**）；`control/stop` **confirm-first**（注入指令，不在 render 時 terminate）。
- **C6**：infer-then-confirm，永不 silent；inferred 值當 editable draft；找不到來源就空白別發明；manifest→語言偵測可靠；非互動補 `--description/--capability/--yes`。
- **C7**：`merge_constellation` 對「新增 body」**append-only PyYAML**（逐字保留 header、零新相依）；edit/remove 才 full re-dump 並印**會丟註解**警告。
- **C8**：`client setup`／`mcp setup` 獨立、**不加 `aether init`**；各自結尾 nudge 指向對方 + help 文案說明 outbound/inbound；`mcp setup` 須警告「star chart 可能空，去跑 client setup 或啟 Observatory」。
- **C9**：MVP 只 `server status`（`redis.ping()` + 誰在 heartbeat）；`up/down` 延後、explicit、docker-gated、echo 命令。
- **C10**：Phase 0 `git init` + `.gitignore`（`__pycache__/`、`.DS_Store`、local `.mcp.json`）+ baseline commit + 每邏輯變更一 commit + `DEVLOG.md`。
- **跨切面 action items**：(a) `.mcp.json` 寫**絕對 python executable**（`sys.executable`）非 bare `python3`（Claude Code venv/PATH 不一定有 mcp/redis 相依）；(b) `cli.py` 先 bootstrap sys.path 再 import 各 `main(argv)`；(c) `poll` dedup 為 in-memory，server 重啟會 reset→可能重現舊回覆（known limitation，spec 註明）；(d) 各 script `main()` 改 `main(argv=None)`，保留 `__main__` guard 與直接執行 fallback。

---

## 共識看板

| # | 子議題 | Claude | Codex | Gemini | 狀態 |
|---|--------|--------|-------|--------|------|
| C1 | packaging now vs -m | 延後(改) | now | 延後 | majority（延後 + shim） |
| C2 | scope 預設 | project | project | project(默認想 local) | agreed（project + 警告 + local 選項） |
| C3 | merge vs claude mcp add | merge | merge | merge | agreed |
| C4 | identity 標準化 | `<p>-mcp` 穩定 | 同 | 同 | agreed |
| C5 | prompts 設計 | reads prefetch/writes 最小指令 | render 不可寫 | 不要 agentic loop | agreed（綜合 = 保守 writes + 無迴圈 + bounded） |
| C6 | client setup metadata | infer-confirm | 同 | 同 + guardrails | agreed |
| C7 | YAML 註解保留 | append-only(改) | ruamel | append-only | majority（append-only PyYAML） |
| C8 | imply / aether init | 獨立、不加 init | 獨立 | 獨立、不加 init | agreed |
| C9 | server up/down | status-only(改) | status-only | status-only | agreed |
| C10 | 變更紀錄機制 | git init+DEVLOG | 同 | 同 | agreed |

---

## 決策紀錄

| # | 決定 | 達成日期 | 依據 Round | 備註 |
|---|------|---------|-----------|------|
| C1 | 延後 packaging；MVP `-m`/絕對 script + 薄 shim | 2026-05-31 | R2 | Gemini blast-radius 論點勝；pyproject 列 Phase 5 |
| C2 | 預設 `--scope project` + 絕對路徑警告 + `--scope local/user` | 2026-05-31 | R2 | 合使用者前選 |
| C3 | 預設 `.mcp.json` 純函式 merge；`claude mcp add` opt-in | 2026-05-31 | R2 | |
| C4 | `mcp setup` 寫穩定 `<project>-mcp`；Body=`<project>` | 2026-05-31 | R2 | 不可 random uuid |
| C5 | reads prefetch / writes 最小指令無迴圈 / control confirm-first / transcript bounded | 2026-05-31 | R2 | Codex+Gemini 安全顧慮綜合 |
| C6 | infer-then-confirm、editable draft、無來源留空、非互動旗標 | 2026-05-31 | R2 | |
| C7 | 新增 body append-only PyYAML；edit/remove full re-dump + 警告 | 2026-05-31 | R2 | 不加 ruamel |
| C8 | 兩指令獨立、不加 init、nudge + help + 空 chart 警告 | 2026-05-31 | R2 | |
| C9 | MVP 只 `server status`；up/down 延後 explicit docker-gated | 2026-05-31 | R2 | |
| C10 | Phase 0 git init + .gitignore + per-change commit + DEVLOG | 2026-05-31 | R2 | |

Action items（跨切面）：(a) `.mcp.json` 寫 `sys.executable` 絕對路徑；(b) `cli.py` 先 bootstrap sys.path；(c) `poll` dedup known limitation 註明；(d) `main(argv=None)` + 保留直接執行 fallback。

---

## 開放問題
- 無 blocker。C2 預設 scope 為 project（合使用者本 session 前選）；若日後要分享給隊友可改 local 避免絕對路徑入庫——留待使用者按需切換，非阻擋。

---

## 下次討論指引

### 進度摘要
2 輪收斂（≤3 限制內）。10 個子議題：8 agreed、2 majority（C1 延後 packaging、C7 append-only PyYAML），無 disputed。最終定案已備妥，下一步走 /write-spec。

### 待處理事項
- /write-spec：依「最終定案」+ action items 產出實作規格（contract-first，8 段）。
- /audit-spec：審查；通過才實作。

### 閱讀建議
- aether/docs/cli/00-plan.md（phasing + critical files）
- 本檔「Round 2 — Synthesis / 最終定案」段
- mcp_server.py（AetherBridge + build_server，prompt 插入點）、core/registry.py（YAML writer 落點）

### 注意事項
- 不破壞 65 測試；argv refactor 後立即全綠驗證。
- writes 不在 prompt render 時送訊息（double-send 風險）。
