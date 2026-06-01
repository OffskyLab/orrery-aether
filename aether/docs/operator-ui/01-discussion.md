---
topic: "Operator 操作畫面（web UI）+ remove/unregister body"
status: consensus
created: "2026-06-01"
updated: "2026-06-01"
participants:
  - Claude (Opus 4.8)
  - 程式碼實證審查（外部 Gemini sandbox-blocked → 第一手讀碼）
  - Codex (GPT-5.4) — 本輪未取得（runtime backgrounded）
facilitator: Claude
rounds_completed: 2
mode: default
premises:
  fixed:        # challengeable: false
    - "UI 是給 operator（寫入）服務；Stargazer 唯讀不變量不可動（不在 Stargazer 加任何寫入路徑）"
    - "operator 維持 localhost + bearer token，不對 LAN 開放"
    - "所有 operator 動作（含新 unregister）必須稽核 emit_operator_action"
    - "不破壞 105 測試；連線 localhost byte-identical"
    - "不引入新驗證機制（沿用 bearer token，不做帳號/session）"
    - "新增 unregister（從 registry 移除 body）在範圍內"
  proposed:     # challengeable: true
    - "UI 由 operator panel 自己 serve（單檔 SPA, FileResponse, GET /）"
    - "unregister = registry hdel only（不連帶清 inbox/heartbeat）"
    - "UI 需要的 read 資料用 operator 自己的 GET endpoints（不去讀 Stargazer）"
    - "token：paste 欄位 → sessionStorage → Bearer header"
    - "confirm-first：terminate / kill_project / unregister"
    - "GET /（頁面）不 gate，寫入動作才 gate"
meta_note: |
  Plan: aether/docs/operator-ui/00-plan.md。Premises 由 Claude 從 goal 推導預設採用（hook 要求推進）；
  user 可在後續 round 翻案。≤3 回合。
  TOOL TRANSPARENCY（依 skill 規則據實記錄，不美化）：R1 外部模型派遣受阻——
  (1) 外部 Gemini CLI 被 sandbox classifier 擋下（會把私有 repo 原始碼送出到 Google API，屬合理 trust-boundary
      防護）；改由 orchestration agent 直接讀碼做「第一手程式碼審查」，findings 已逐條對照真實檔案驗證。
  (2) Codex（codex-rescue）連兩次被 companion runtime 放到 background、只回 placeholder，無法取得同步輸出 →
      本輪 Codex 視為未取得。因此 R1 實為「Claude + 程式碼實證審查」兩個獨立視角；audit-spec 會再嘗試外部 Codex。
---

# Operator 操作畫面（web UI）+ remove/unregister body

## 議題定義

### 背景
operator panel 目前是 token-gated 的 **純 HTTP 寫入 API**（`:8770`，inject/pause/resume/terminate/
kill_project），**無畫面**。Stargazer 是**唯讀** SPA（FileResponse 單檔）。使用者要一個 operator 的
**操作畫面**，涵蓋所有 op + 新增「移除 registry body」。

### 目標
(A) 新增 `unregister` 寫入 op（`Registry.remove` + 稽核）+ `POST /unregister`；(B) operator panel
自 serve 一個單檔 SPA，提供所有 op 的控制 + 必要的 read（body 清單/對話）。

### 範圍
做：unregister op/endpoint、operator UI（單檔）、UI 所需 read endpoints、稽核、confirm-first、測試、文件。
不做：改 Stargazer（不加寫入）、新驗證機制、對外暴露、帳號系統。

### 子議題（中性編號）
- [ ] C1：UI 落點（operator self-served vs Stargazer SPA vs 分離）+ `GET /` 是否 gate
- [ ] C2：`unregister` 語意（registry hdel only vs 連帶清 inbox/heartbeat/hold/proclog；online 時 refuse/warn）
- [ ] C3：UI 需要的 read 資料 + read endpoints 放哪（operator 自己 GET vs 讀 Stargazer API）
- [ ] C4：瀏覽器 token UX/安全（paste+sessionStorage vs per-action prompt；CSRF；`?token=`）
- [ ] C5：confirm-first 範圍 + 稽核 + 破壞性操作防呆

---

## 討論紀錄

### Round 1

#### Claude (Opus 4.8)

**C1 — UI 落點 + GET / gate**
- 立場：**operator panel 自 serve 單檔 SPA**（`GET /` 用 `FileResponse(operator_panel/web/index.html)`，
  mirror Stargazer 的做法），**不**放進 Stargazer。`GET /` 開放（不 gate）；所有**寫入**動作才 token-gated。
- 論點：
  1. read/write 分離是核心設計柱（Stargazer 唯讀不變量有對抗測試 `test_p3_scenario_6_readonly`）。把
     寫入按鈕放進 Stargazer SPA、再 POST 到 :8770，會模糊這條線、且要跨 port + CORS。
  2. self-served → UI 與寫入 API **同源**（無 CORS）、同一把 token、co-located。
  3. `GET /` 開放是合理的：localhost-only + 動作需 token；若連頁面都 gate，瀏覽器要從哪拿 token 才能載入？
     徒增摩擦。
- 證據：`stargazer/server.py:92-97`（FileResponse 模式）、`operator_panel/server.py` require_token 在寫入端。

**C2 — unregister 語意**
- 立場：**registry `hdel` only**（最小、可逆）；**不**自動清 inbox/heartbeat/hold（獨立的生命週期/資料，
  清 inbox 可能丟未投遞訊息=資料遺失）。加守門：body **目前 online（heartbeating）時 refuse 除非 `force`**
  （否則它會被 Observatory 下一輪 re-register，等於跟活著的程序打架、且「刪了又冒出來」很困惑）。
- 論點：移除 registry entry = 「讓它從 who/Stargazer 消失」這個語意；inbox/heartbeat 與之正交。surgical +
  稽核;要清資料另開 op。online-guard 防「刪了又回來」。
- 證據：`registry.py:146`（remove=hdel）、heartbeat key 獨立、`emit_operator_action` 稽核。

**C3 — read endpoints**
- 立場：operator panel **自己**加最小 GET（`GET /api/bodies` → id+online；`GET /api/conversations` →
  events 近期 thread）。它本來就有 redis client，**讀沒問題**（唯讀不變量是針對 Stargazer，不是全域禁讀）。
  這些 read **也 token-gate**（整個 panel 是特權；UI 先輸 token 再載清單）。**否決**「UI 去讀 Stargazer API」
  （耦合兩服務 + CORS + 要兩個都跑）。
- 證據：operator 用 writable redis（可讀）;stargazer 的 read endpoints 可當實作參考。

**C4 — token UX/安全**
- 立場：token **paste 欄位 → sessionStorage**（關分頁即清）→ 每次請求帶 `Authorization: Bearer`。**不**用
  `?token=` query（會洩漏到 log/history）。CSRF 不適用（token 在 header、無 cookie）。**永不 log token**。
- 證據：`require_token` 讀 Authorization header。

**C5 — confirm-first + 稽核**
- 立場：**confirm-first**（JS confirm；最危險的 unregister/kill 用「打字確認」）用於 `terminate /
  kill_project / unregister`。所有 op 稽核（既有 `emit_operator_action` + 新 unregister）。UI 顯示回應/稽核結果。
- 論點：破壞性操作要刻意的一步;對齊 `/mcp__aether__stop` confirm-first 先例。
- 證據：`mcp_server` stop prompt（confirm-first）、`emit_operator_action`。

#### Codex (GPT-5.4) — 未取得
本輪 codex-rescue 連兩次被 companion runtime 放 background、只回 placeholder，無同步輸出（見 meta_note）。
未獨立取得 Codex 視角；下方「程式碼實證審查」與 Claude 已覆蓋同一面;audit-spec 會再嘗試外部 Codex。

#### 程式碼實證審查（外部 Gemini 被 sandbox 擋；改為第一手讀碼，逐條對照檔案驗證）
總體：**同意 Claude C1–C5 的方向**，但補 6 個 plan/Claude 漏掉、且已驗證的盲點：

- **C1 補（packaging 404，高）**：`pyproject.toml` 的 package-data 只有 `"aether.stargazer" = ["web/*"]`，
  **沒有** `aether.operator_panel` 的 web → 新增的 `operator_panel/web/index.html` **不會進 wheel**，
  pip/pipx 裝好後 `GET /` 會 404。self-served SPA 對，但 plan 漏了打包這行。
- **C4 補（XSS 竊 token，高）**：`stargazer/web/index.html`（renderSky ~108-114 / renderTimeline ~123-138
  / renderExtinction ~149-151）把不可信的 `en.from`/`en.to`/`b.text`/`b.intent`/body id/`ev.reason` 直接
  字串插進 **innerHTML**、零跳脫。operator UI 若照抄同模式、又把 bearer token 放 sessionStorage → 惡意 body
  id/訊息文字 = stored XSS 偷走 token。**operator SPA 對不可信資料必須用 `textContent`/跳脫,不可 innerHTML。**
- **C3 補（_online_map 重複）**：`stargazer/server.py:38-40` 已用 `hgetall REGISTRY_KEY` + `exists
  heartbeat:<id>` 算 online。operator `GET /api/bodies` 會原樣重寫一份 → 抽到 `core` 共用 helper，兩邊都呼叫。
- **C2 補（unregister vs self-heal + 殘影）**：`mcp_server.py:87-101` 每 ~10s `registry.add` 重加自己的
  session body、`cleanup()` 退出才 remove → unregister 一個**活著的** body 會在 ~10s 內彈回。`terminate`/
  `kill_project` 只設控制旗標(`control.py:63-67`)、**不碰 registry**。所以 unregister 的真正用途是**死的/陳舊**
  body;且應**同時刪 `heartbeat:<id>` key**,否則 `_online_map` 會短暫顯示 ghost online。online-guard 對。
- **C5 補（confirm-first 只是裝飾）**：直接 `curl` 打 token-gated endpoint 會略過 JS confirm → **真正的控制
  是 server 端稽核(`emit_operator_action`)+ token,不是 confirm 對話框**。confirm 是 DX,不是安全。
- **C1/test 補（隔離閘門復用）**：`test_p4_operator.py` 已斷言 Stargazer 路由 ⊆ {GET,HEAD}、無 inject/control
  路由。新 operator GET/POST 要保持這些綠,並加「`GET /api/bodies` + `POST /unregister` 無 token → 401」測試。

### Round 2 — Synthesis（Claude, facilitator）

兩個視角（Claude + 程式碼實證審查）**方向一致、無根本分歧**;審查補的 6 點全部採納為最終立場。
（Codex 本輪未取得,見 meta_note;此議題無 disputed 點,audit-spec 會再上外部 Codex。）

**收斂後最終立場（各子題 → agreed）:**
- **C1**:operator panel **自 serve 單檔 SPA**（`GET /` 用 `FileResponse(operator_panel/web/index.html)`,
  頁面開放、寫入動作 token-gated）。**🔴 必須在 `pyproject.toml` 加 `"aether.operator_panel" = ["web/*"]`**,
  否則 pip/pipx 裝好 `GET /` 會 404。不放進 Stargazer。
- **C2**:`unregister` = `Registry.remove`（registry `hdel`）**+ 刪 `aether:heartbeat:<id>`**（免 `_online_map`
  殘影）+ 稽核;**online-guard**:body 仍 heartbeating 時 **refuse 除非 `--force`/`force:true`**（活著的會被
  re-register 彈回);**不**清 inbox/hold/proclog（資料,可能丟未投遞訊息）。註:`terminate`/`kill_project` 只設
  控制旗標、不碰 registry,用途不同。
- **C3**:operator 自己加 `GET /api/bodies`（id+online）+ `GET /api/conversations`（events 近期 thread）,
  **token-gated**;**把 online-map 邏輯抽到 `core` 共用 helper**（Stargazer + operator 共用,不重複實作）。
- **C4**:token **paste → sessionStorage → `Authorization: Bearer`**;不用 `?token=`;**🔴 SPA 對所有不可信資料
  （body id / 對話文字 / reason …）一律用 `textContent`/跳脫,嚴禁 innerHTML 插值**(否則 stored XSS 偷 token);
  永不 log token。
- **C5**:`terminate / kill_project / unregister` 在 UI **confirm-first**（unregister/kill 用打字確認）;但明示
  **confirm 只是 DX,真正的安全控制是 server 端 token + 稽核**（curl 可略過 JS confirm）。UI 顯示稽核結果。

**Rejection log（HARD）:**
- ❌ 把 operator 控制放進 Stargazer SPA → 拒絕（危及唯讀不變量）;operator UI 留在 operator panel。
- ❌ unregister 連帶清 inbox/hold/proclog → 拒絕（資料遺失、超範圍）;只 hdel registry + del heartbeat。
- ❌ 把 client-side confirm 當成安全控制 → 拒絕（裝飾性）;以 server token + 稽核為準。
- ❌ `?token=` query → 拒絕（洩漏到 log/history）;用 Authorization header。
- ❌ 在 operator endpoint 重寫 `_online_map` → 拒絕;抽 `core` 共用 helper。

**事實修正（納入）:** operator_panel **目前無 `web/` 目錄**且 **pyproject 未打包它** → 兩者都要補,否則
裝好的 UI 取不到頁面。

---

## 共識看板

| # | 子議題 | Claude | 程式碼審查 | Codex | 狀態 |
|---|--------|--------|----------|-------|------|
| C1 | UI 落點 + GET / gate | operator self-served, GET / 開放 | 同意 + **補打包 web/*（否則 404）** | 未取得 | **agreed** |
| C2 | unregister 語意 | hdel + online-guard | 同意 + **加刪 heartbeat key（免殘影）** | 未取得 | **agreed** |
| C3 | read endpoints | operator GET, token-gated | 同意 + **抽 core 共用 online-map** | 未取得 | **agreed** |
| C4 | token UX/安全 | paste→sessionStorage→Bearer | 同意 **但 SPA 必須 textContent/跳脫（否則 XSS 偷 token）** | 未取得 | **agreed** |
| C5 | confirm-first + 稽核 | terminate/kill/unregister confirm | 同意 **但 confirm 只是 DX,真控制是 token+稽核** | 未取得 | **agreed** |

> 兩視角一致（皆有 file:line evidence）→ consensus。Codex 本輪未取得(meta_note);無 disputed 點,
> 故標 agreed,並由 audit-spec 的外部 Codex 補第三方驗證。

---

## 決策紀錄

| # | 決定 | 達成日期 | 依據 Round | 備註 |
|---|------|---------|-----------|------|
| D1 | operator 自 serve 單檔 SPA（FileResponse GET /，頁面開放、寫入 token-gated）；**pyproject 加 `aether.operator_panel` web/* 打包** | 2026-06-01 | R2 | 不放 Stargazer;否則 pip 裝後 404 |
| D2 | `unregister` = registry hdel + 刪 heartbeat key + 稽核;online-guard（heartbeating 時 refuse 除非 force）;不清 inbox/hold/proclog | 2026-06-01 | R2 | terminate/kill 不碰 registry,用途不同 |
| D3 | operator 加 token-gated `GET /api/bodies` + `GET /api/conversations`;online-map 抽 `core` 共用 helper | 2026-06-01 | R2 | 不重複 stargazer 實作 |
| D4 | token paste→sessionStorage→Bearer;**SPA 不可信資料一律 textContent/跳脫,禁 innerHTML**;不用 `?token=`;不 log | 2026-06-01 | R2 | XSS 竊 token 防護 |
| D5 | terminate/kill_project/unregister confirm-first（DX）;真正控制 = server token + 稽核 | 2026-06-01 | R2 | curl 可略過 confirm |

---

## 開放問題

1. **(小)** online-map 共用 helper 放哪（`core/registry` 旁 vs 新 `core/online.py`）— 實作細節,spec 定。
2. **(延後)** 更細的角色/權限（多 operator、唯讀 vs 可寫角色）— MVP 單一 token,不做。
3. **(延後)** unregister 之外的 registry 編輯（改描述/capabilities）— 本次只做 remove。

---

## 下次討論指引

### 進度摘要
R1（Claude + 程式碼實證審查）+ R2 synthesis 完成。C1–C5 全 **agreed**（含 6 點補強）,status=consensus。
Codex 本輪未取得（meta_note）;audit-spec 會補外部審查。

### 待處理事項
`/write-spec`:把 D1–D5 + rejection log 寫成 8 段契約 + 驗收（含 404-packaging、XSS-textContent、
unregister hdel+heartbeat+online-guard、隔離閘門仍綠、無 token→401）。

### 閱讀建議
本檔 R2、00-plan、operator_panel/server.py、registry.py:146、stargazer/server.py:38-40,92-97、
stargazer/web/index.html、pyproject.toml（package-data）、test_p4_operator.py（隔離閘門）。

### 注意事項
2 視角收斂（≤3 達標）;fixed 前提守住;agreed 皆具 evidence;Codex 第三方驗證延到 audit-spec。
