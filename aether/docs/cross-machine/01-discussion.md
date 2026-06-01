---
topic: "Aether 跨機部署（Redis auth+TLS / register / 遠端訂閱）"
status: consensus
created: "2026-06-01"
updated: "2026-06-01"
participants:
  - Claude (Opus 4.8)
  - Codex (GPT-5.4)
  - Gemini
facilitator: Claude
rounds_completed: 2
mode: default
premises:
  fixed:        # challengeable: false
    - "F1/F2/F3 三能力都要做（不辯論要不要做，只辯論怎麼做）"
    - "既有 per-project inbox + consumer-group 隔離 / exactly-once 不可退化（F3 不可變共享佇列+應用層過濾反模式）"
    - "localhost 開發路徑與現狀 byte-identical（無 env 時行為不變）"
    - "Redis HA / clustering 仍 out of scope（只做部署，不做高可用）"
    - "不可破壞 88 測試與安全不變量（Stargazer 唯讀 / operator localhost+token / recipient guard）"
    - "秘密只放 gitignored .env，不進版控"
  proposed:     # challengeable: true
    - "F2 形態：register(does-both) vs bus use+client setup(split) vs 擴充 client setup"
    - "端點持久化到本機 gitignored config（~/.aether/ 或 repo .aether/）"
    - "跨機星圖：sync() delete-all 改 additive vs 單一共享完整 constellation"
    - "F3 = 遠端 bus 訂閱自己 inbox（推薦解讀）；register 是否自動啟動 Observatory"
    - "安全：單一共享密碼起步（ACL 後續）/ 自簽 CA / TLS-only vs 雙埠"
    - "遠端 body 的 working_dir 填 null/省略"
meta_note: |
  Plan seed: aether/docs/cross-machine/00-plan.md。User goal 限定 ≤3 回合。
  Premises 由 Claude 從 goal + plan 推導後預設採用（user 未逐條確認，hook 要求推進）;
  user 可在後續 round 翻案。
---

# Aether 跨機部署（Redis auth+TLS / register / 遠端訂閱）

## 議題定義

### 背景
單機 Aether 已運作（Redis bus + Observatory + MCP）。要支援 A=bus、B/C 各自跑專案，
跨機互通。已驗證：`working_dir` 純本機（不入 envelope）、路由是邏輯名、`make_redis`
無 password/TLS、`sync()` 是 delete-all、Redis 現為 0.0.0.0 無認證。

### 目標
F1 Redis 密碼+TLS；F2 其他主機 `aether register --host --port` 加入匯流排；
F3 client 訂閱 MQ 並只處理屬於自己的訊息。

### 範圍
做：連線安全、端點持久化、跨機 body 註冊、遠端訂閱、跨機星圖一致性、文件。
不做：Redis HA、公網滲透防護（建議 VPN/tunnel）、NAT 穿透。

### 子議題（中性編號，無排序）
- [ ] C1：F2 命令形態 + 端點持久化位置
- [ ] C2：跨機星圖一致性（`sync()` delete-all → additive / 單一共享 constellation）
- [ ] C3：F3 語意（遠端訂閱自己 inbox vs 共享佇列過濾）+ register 是否自動起 Observatory
- [ ] C4：安全深度（密碼 vs ACL / 自簽 vs CA / TLS-only vs 雙埠）
- [ ] C5：遠端 body 的 `working_dir` 處理

---

## 討論紀錄

### Round 1

#### Claude (Opus 4.8)

**C1 — F2 命令形態 + 端點持久化**
- 立場：**分離關注 + 提供便利糖**。`aether bus use --host --port [--tls] [--password-env]`
  持久化「本機 bus profile」；`aether client setup` 仍負責註冊 body（已存在，cli.py:101）；
  `aether register` = 一次跑 `bus use`+`client setup` 的便利包。
- 論點：
  1. 端點（bus 在哪）與身分（我是哪個 body）**生命週期不同**：端點很少改、身分常更新；
     綁成單一 verb 會把兩種失敗模式混在一起。
  2. **真正缺的原語是「持久化」**：cli.py 多個子指令已有 `_add_redis_opts`（行 42–45、
     202/213/218/222），但沒人把端點存起來 → 每條指令都要重打 `--host/--port`。應加
     `_load_bus_profile()`，在 argparse 預設之前把 profile 值灌進去，讓 observatory/send/
     who/mcp 全部繼承。
  3. 持久化位置：`~/.aether/config.json`（**user/機器層級**，絕不入 repo）；理由：跨機是
     機器特性，不該污染共享的 repo。
- 證據：`cli.py:42-45,101,202`；`make_redis` 單一入口 `aether_client.py:52`。

**C2 — 跨機星圖一致性**
- 立場：**registry-as-truth + additive 註冊**。把 `sync()` 預設改成「不刪別人」，新增
  `register_body`（單一 hset，不 wipe），給 client setup / register 用；保留 `--prune`
  顯式選項給「單一擁有者做完整 sync」的場景。`constellation.yaml` 退化為「**本機** body
  的種子」，不再是跨機唯一真相。
- 論點：
  1. 現況 `sync()` 做 `delete(REGISTRY_KEY)` 再全加（registry.py:66-67）→ B 用自己的局部
     constellation 同步會**刪掉 A/C 的 body**。
  2. heartbeat 每 tick 重加自己的 body（已存在的自癒）→ additive 與既有行為**一致**，不是
     新概念。
  3. 「單一共享完整 constellation」也行，但要求每台都同步到每台的檔案 → 部署很脆（漏同步就
     路由錯）。registry-as-truth 較鬆耦合。
- 證據：`registry.py:66-67,87`（add 已是單 hset）、heartbeat 重加邏輯（前述）。
- 風險：兩台註冊同一 id → 該 hash field last-write-wins；可接受或加警告。

**C3 — F3 語意 + register 是否自動起 Observatory**
- 立場：F3 = **「把我的 inbox consumer 跑在遠端安全 bus 上」**。「是不是我的」過濾 = stream
  名 `aether:inbox:<self>`（已精確）。**否決**共享佇列+應用層過濾（退化隔離/exactly-once/
  recipient guard，違反 fixed 前提）。`register` **不**自動起 Observatory。
- 論點：
  1. per-inbox + consumer group 本來就是「只收自己的」，共享佇列是退步。
  2. 註冊（設定）與上線（生命週期、會觸發 `claude -p`、有成本）應分離；register 完印出
     `→ aether observatory <id>` 提示即可。
  3. 加可觀測性：上線時 log「subscribed to <bus> as <id>」；可選在 broadcast 流發 `hello`
     公告，讓既有節點看得到新成員。
- 證據：`observatory/main.py`（grp-<id> / inbox_stream）、`progress.py`（telescope）、
  broadcast 流已存在（Wave）。

**C4 — 安全深度**
- 立場：**單一共享密碼（requirepass）+ TLS（自簽 CA、附 make-certs 腳本+文件）起步**；
  Redis ACL 每身分使用者**延後**（信任主機間 YAGNI）。TLS **雙埠遷移**：6379 留給
  localhost dev（backward-compat，fixed 前提），6380 開 TLS 給遠端；文件教 prod 把 6379
  綁 loopback。`make_redis` 加 `password / ssl / ssl_ca_certs`（+可選 client cert mTLS）。
- 論點：
  1. `make_redis` 是唯一連線入口（aether_client.py:52）→ 改一處、env 灌全部。
  2. `.env` 已有 operator token 先例 → 密碼同樣放 gitignored `.env`。
  3. 雙埠讓 localhost 行為 byte-identical（不破壞既有 88 測試）。
- 證據：`aether_client.py:52`、`.env.example` / docker-compose（operator token 模式）。

**C5 — 遠端 body 的 working_dir**
- 立場：你註冊但**不在本機跑**的 body → working_dir **null/省略**。每台只為「自己會跑
  Observatory 的 body」填本機有效路徑。
- 論點：
  1. Observatory 只讀**自己那個 body** 的 working_dir（run_observatory.py:73）→ 遠端 body
     的 null 對路由完全無害。
  2. 加守門：對 working_dir 為 null/不存在的 body 啟 Observatory → 警告/拒絕
     （run_observatory.py:74-75 已有「不存在則 WARNING」的雛形，升級為對 null 明確處理）。
- 證據：`run_observatory.py:73-75`、`registry.py`（working_dir 為 Optional）。

#### Codex (GPT-5.4)
- **C1** 同意 split+sugar、`~/.aether/config.*`。Precedence 必為 **flags > env > profile >
  localhost default**。⚠️ profile loader 掛在 CLI argparse **不夠**——passthrough
  (observatory/send/consult/mcp serve) 繞過 `build_parser()`（`cli.py:197-202,257-263`）、
  observatory 直讀 env（`run_observatory.py:56-58`）;loader 須在「組 kwargs」層或各
  forwarded entry 共用。
- **C2** registry-as-truth + additive。⚠️ 不只 client setup,**Observatory 啟動也呼叫
  destructive sync**（`run_observatory.py:84-86`)——兩處都要改。`Registry.add` 已是單 body
  additive（`registry.py:87-90`）。Duplicate id 需偵測(共用 inbox/group,`main.py:77-80`)。
- **C3** F3=遠端訂閱自己 inbox;否決共享佇列;register 不自動起(給 `--start` 或印指令)。
  ⚠️ `run_observatory` 無限迴圈**無 Redis 例外包裹**（`run_observatory.py:117-123`)——遠端
  TLS 斷線會逃出迴圈;需 reconnect/backoff + 重連後 `recover_pending`/`claim_pending`
  （`main.py:333-340`、`aether_client.py:184-207`)。
- **C4** 共享密碼+TLS+自簽 OK,但:預設維持 plaintext localhost;0.0.0.0 明文 6379 不可留;
  **TLS over IP 需 cert SAN 含該 IP**（`--host 172.16.100.55` 否則驗證失敗);AOF+auth 不保護
  at-rest,文件化磁碟暴露;**反對完全延後 ACL 的 API shape**——現在就加 optional `username`
  欄(docs 可先單一 user)。
- **C5** remote working_dir null/omit;**null/invalid 應 hard error 而非 warn**(否則在錯目錄
  跑 claude)。`client setup` 一律寫 cwd（`cli.py:120-126`)只對本機 body 正確。
- Blind spots: passthrough 繞 argparse;`run_observatory.py:85` 仍 wipe;duplicate id;remote
  TLS drop 逃迴圈;secret distribution flow(password+CA 要先存在否則 ping 失敗);heartbeat
  TTL 30s 跨機延遲;`aether:events` MAXLEN~50000 近似裁剪——**F3 勿建在 event stream 上**。

#### Gemini
- **C1** 同意 split+sugar+`~/.aether`。**關鍵事實修正:`make_redis` 不是唯一入口**——
  `stargazer/server.py:176`、`operator_panel/server.py:113` 直建 `redis.Redis(...)` 繞過它;
  Claude C1-2 / C4-1「改一處」**不成立**。Precedence **flag>env>profile>default**;profile
  若蓋 env 會破壞 docker-compose 容器內 `AETHER_REDIS_HOST=redis`（`docker-compose.yml:42,65`)。
  **register 原子性**:先用記憶體 endpoint `ping`(含 auth/TLS)通過才落盤 profile,否則半完成
  污染。位置 `~/.aether/profiles/<name>.json`+default(MVP 可單 profile,schema 預留 `name`)。
- **C2** 同意 registry-as-truth+additive,但**兩個 hot-path 呼叫點都要改**（`cli.py:135`、
  `run_observatory.py:85`)。**Duplicate id = split-brain,不可接受 last-write-wins**:id 推導
  inbox/group（`main.py:78-80`),撞 id → 同訊息被兩台搶、50% 靜默誤投;`working_dir`/`inbox`
  整包被後寫覆蓋（`registry.py:29-35`)。須 **fail-closed**(拒絕或 `--force`)。
- **C3** 完全同意。補:**PEL 是 server-side,TLS 重連後 `XAUTOCLAIM` 認領回來**(group/consumer
  名穩定=project_id)→ 現有 exactly-once 跨機原封成立,正面駁斥「跨機要改共享佇列」。**auto-start
  安全反論**:Observatory 可被任何已註冊 body 的 Comet 觸發 `claude -p`(`--allow-write` 更危險,
  `run_observatory.py:62-63`),guard 只擋未註冊 sender(`main.py:266-268`)→ register 不該自動起。
  `hello` 須在 additive register **進 registry 之後**才發,否則收方回覆撞 invalid_recipient;hello
  是非 solicit Wave→強制不回(`main.py:227-237`),不會風暴。
- **C4** 同意共享密碼+自簽+雙埠+ACL 延後。**6379 現況 `"6379:6379"` 無 loopback 綁定**(對比 web
  的 `:45,:68`)——雙埠須改 `127.0.0.1:6379:6379`,忘了=無認證明文後門對 LAN,**須入 AC+測試**。
  **cert rotation 單點故障**:server cert 過期所有 client 同時 fail,ping 失敗只 sys.exit 無預警
  (`run_observatory.py:79-82`)。**兩套 secret store**:bus 機容器讀 `.env`(讀不到 `~/.aether`),
  client 機讀 profile——輪替同步須明確。**共享密碼 ≠ sender 認證**:`emit` 不認證 from_
  (`aether_client.py:68-84`),guard 只擋未註冊 to → 須明示「shared-password does NOT give you」。
- **C5** 同意 null/省略+升級守門。**守門對 null 與 missing 分流**:現況只 warn truthy-but-missing
  (`run_observatory.py:74-75`),**null 靜默通過 → claude 在錯 cwd 跑**;null 應 fail-closed。
  `client setup` 寫死 cwd（`cli.py:120-121`)是跨機污染源 → registry-as-truth 勝出第三證據。
- **Time skew**: heartbeat TTL 由 **Redis server 時鐘**計(`heartbeat.py:29`),client skew 不影響
  過期(好消息);但 TTL=30s 跨機高延遲可能誤判離線→hold;文件化「TTL > 2× 最壞 RTT + beat interval」。

### Round 2 — Synthesis（Claude, facilitator）

三方在 C1–C5 **方向一致**(無根本分歧);Codex/Gemini 多為「同意 + 補盲區」。本輪收斂並把盲區
納入最終立場;依規則附 **rejection log**。

**收斂後最終立場(各子題 → agreed):**
- **C1**:`aether bus use`(持久化端點) + 既有 `client setup`(註冊 body) + `aether register`=兩者
  便利包。端點存 `~/.aether/config.json`(MVP 單 profile,schema 預留 `name`)。Precedence
  **flag > env > profile > default**(profile 不得蓋 env,否則破壞容器內 `AETHER_REDIS_HOST=redis`)。
  連線 kwargs 集中於一個 builder,讓 passthrough(observatory/send/consult/mcp serve)+
  **stargazer/operator 兩個直建入口**都吃到。`register` 先記憶體 `ping`(含 auth/TLS)通過才落盤。
- **C2**:registry-as-truth。新增 `register_body`(單 hset 不 wipe);**`cli.py:135` 與
  `run_observatory.py:85` 兩個 `load_and_sync` 呼叫點都改 additive**;`--prune` 留給單一擁有者完整
  sync。**Duplicate id fail-closed**(已存在且指紋不同 → 拒絕,需 `--force`),不可 last-write-wins
  (避免共用 inbox/group 的 split-brain)。
- **C3**:F3 = 遠端 bus 訂閱自己 inbox;**否決**共享佇列+過濾。`register` **不**自動起 Observatory
  (生命週期 + 安全:auto-start 等於預設打開「被已註冊 body 觸發 claude」的面)。消費迴圈加
  **reconnect/backoff**,重連後跑 `recover_pending`(PEL server-side,exactly-once 跨機原封)。
- **C4**:requirepass 共享密碼 + TLS(自簽 CA、`make-certs`)+ **雙埠**(6379 改 `127.0.0.1` loopback、
  6380 對外 TLS)。`make_redis` 加 `password/ssl/ssl_ca_certs` + optional `username`(ACL-ready),
  **stargazer/operator 兩處同步加**。cert 須含 **IP SAN**;`make-certs` 印到期日。文件化:cert 到期=
  全網斷線單點故障、bus(.env)vs client(profile)兩套 secret store、**「共享密碼 ≠ sender 認證」**不
  保證清單。
- **C5**:遠端 body `working_dir` 顯式 **null**。Observatory 啟動守門升級:選定 body 的 working_dir 為
  null → **hard error**(此 body 標記遠端、不應在此啟動);missing-dir 維持錯誤。`client setup` 只為
  本機 body 寫 cwd。

**Rejection log（HARD requirement on synthesis rounds）:**
- ❌ **register 時在 broadcast 發 `hello`/announce**(Claude 自己 R1-C3-3 + Gemini 延伸):移出 MVP——
  F1/F2/F3 不需要;`who`/registry 已讓新成員可見;徒增 Wave-policy 面。需要再議。
- ❌ **多 profile `~/.aether/profiles/<name>.json` + default 指標**(Gemini C1):MVP 拒絕,用單一
  `config.json`;A/B/C 拓樸 YAGNI。
- ❌ **mTLS client 憑證**(Codex C4):MVP 拒絕,server-auth TLS + 密碼對信任主機足夠。
- ❌ **改 heartbeat 預設 TTL**(Gemini):拒絕——動到 localhost byte-identical 前提;TTL=30s 維持。
- ❌ **現在就建 ACL users/設定**(Codex):拒絕 enforcement,延後。

**部分採納（modify）:**
- 🟡 多 profile → 只在 schema **預留** `name`/`profiles` 欄(向後相容),不實作切換。
- 🟡 mTLS → `make_redis` 留 `ssl_certfile/ssl_keyfile` optional pass-through 參數,不建流程/文件。
- 🟡 heartbeat TTL → 不改預設,但**文件化**「跨機 ttl > 2× 最壞 RTT + beat interval」+ 暴露
  `--heartbeat-ttl`(若尚無)。
- 🟡 ACL → 只收 `make_redis` 的 optional `username` 參數形狀,讓 ACL 日後不被卡。

**事實修正(納入)**:`make_redis` 非唯一 Redis 入口——`stargazer/server.py:176`、
`operator_panel/server.py:113` 直建 client;所有「改一處」改為「一個共用 builder + 這兩處」。

**仍待拍板的小決策(非阻斷,spec 採預設):** client 端密碼存哪——預設 **profile 只存
host/port/tls/ca-path,密碼走 env(`AETHER_REDIS_PASSWORD`)**,避免 `~/.aether` 明文存密碼;若選擇
存 profile 則檔案 `chmod 600`。

---

## 共識看板

| # | 子議題 | Claude | Codex | Gemini | 狀態 |
|---|--------|--------|-------|--------|------|
| C1 | F2 形態+持久化 | split+sugar, ~/.aether, precedence flag>env>profile | 同意+precedence+passthrough loader | 同意+make_redis 非唯一入口+register 原子性 | **agreed** |
| C2 | 跨機星圖 | registry-as-truth+additive | 同意+兩個 sync 呼叫點 | 同意+duplicate-id=split-brain fail-closed | **agreed** |
| C3 | F3 語意 | 遠端訂閱自己 inbox；不自動起 | 同意+reconnect/backoff | 同意+PEL 跨機原封+auto-start 安全反論 | **agreed** |
| C4 | 安全深度 | 共享密碼+TLS自簽+雙埠 | 同意+IP SAN+ACL shape now | 同意+6379 loopback 回歸+cert rotation SPOF | **agreed** |
| C5 | 遠端 working_dir | null+守門 | 同意+null hard error | 同意+client setup cwd 污染 | **agreed** |

> 全部 `agreed`:三方方向一致且**每子題皆有具體 file:line evidence**(見 R1/R2)。狀態 → consensus。

---

## 決策紀錄

| # | 決定 | 達成日期 | 依據 Round | 備註 |
|---|------|---------|-----------|------|
| D1 | F2=split(`bus use`+`client setup`)+`register` 便利包;端點存 `~/.aether/config.json`;precedence flag>env>profile>default | 2026-06-01 | R2 | 連線 kwargs 集中一個 builder,passthrough + stargazer/operator 都吃到;register ping-before-persist |
| D2 | registry-as-truth + additive `register_body`;改 `cli.py:135`+`run_observatory.py:85` 兩個 destructive sync;`--prune` 顯式;duplicate-id **fail-closed** | 2026-06-01 | R2 | 避免共用 inbox/group split-brain |
| D3 | F3=遠端訂閱自己 inbox(否決共享佇列);`register` 不自動起 Observatory;消費迴圈加 reconnect/backoff + 重連後 recover_pending | 2026-06-01 | R2 | PEL server-side,exactly-once 跨機原封 |
| D4 | requirepass 密碼 + TLS(自簽 CA/make-certs/IP SAN)+ 雙埠(6379→127.0.0.1, 6380 TLS);`make_redis`+stargazer+operator 三處加 password/ssl/ssl_ca_certs/optional username | 2026-06-01 | R2 | 文件化 cert SPOF + 兩套 secret store + 「共享密碼≠sender 認證」 |
| D5 | 遠端 body `working_dir` 顯式 null;Observatory 啟動對 null/missing **hard error** | 2026-06-01 | R2 | `client setup` 只為本機 body 寫 cwd |

---

## 開放問題

1. **(小、非阻斷)** client 端密碼存哪:預設「profile 不存密碼,走 env `AETHER_REDIS_PASSWORD`」;
   若改存 profile 須 `chmod 600`。spec 採預設,user 可推翻。
2. **(延後)** 跨信任域時的 Redis ACL per-identity users — MVP 只預留 `username` 參數形狀。
3. **(延後)** 新成員 discovery/`hello` 公告 — 移出 MVP,需要再開議題。

---

## 下次討論指引

### 進度摘要
R1(Claude/Codex/Gemini)+ R2 synthesis 完成。C1–C5 全 **agreed**,status=consensus。
3 個盲區事實修正已納入(make_redis 非唯一入口、兩個 sync 呼叫點、duplicate-id split-brain)。

### 待處理事項
進入 `/write-spec`:把 D1–D5 + rejection log + 開放問題預設 寫成 8 段契約 + 驗收條件
(含「6379 loopback」「無 env byte-identical」「additive 不刪別人」「null working_dir hard error」測試)。

### 閱讀建議
本檔 R2 synthesis、00-plan.md「Affected files」、`stargazer/server.py:176`、
`operator_panel/server.py:113`、`registry.py:66-90`、`run_observatory.py:84-123`。

### 注意事項
2 回合即收斂(≤3 達標);fixed 前提守住;agreed 皆具 evidence。
