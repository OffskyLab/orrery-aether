# Aether 跨機部署（Redis auth+TLS / register / 遠端訂閱）

## 來源

- 計畫：`aether/docs/cross-machine/00-plan.md`
- 討論（consensus, 2 rounds, D1–D5 + rejection log）：`aether/docs/cross-machine/01-discussion.md`

---

## 目標

讓 Aether 從「單機」走到「A 當匯流排、B/C 各跑自己的專案」的跨機部署：(F1) Redis 連線要有
**密碼 + TLS**；(F2) 其他主機能用一條命令**指向遠端 bus 並註冊自己的 body**；(F3) client 在
**遠端、加密、需認證**的 bus 上**訂閱自己的 inbox**並自動處理屬於自己的訊息。

🔴 **不變量的精確範圍（audit 釐清）**：所謂「localhost byte-identical」**僅指 Redis 連線行為**
——沒有設定任何 `AETHER_*` 連線環境變數時，`make_redis()` 不帶 ssl/auth kwargs、連線方式與現狀
完全一致。以下兩項是**刻意的行為變更**（安全/正確性），**不**受 byte-identical 約束，視為本 task
的明確變更：(a) `Registry.sync` 由 delete-all 改 additive；(b) Observatory 對 null/不存在
`working_dir` 由 WARNING 升為 hard error。其餘既有 per-project 隔離 / exactly-once / 安全不變量
不得退化。

---

## 介面合約（Interface Contract）

### 1. `make_redis()` — 唯一的 Redis client 建構點（core/aether_client.py）

```python
def make_redis(host="localhost", port=6379, db=0, *,
               password=None, username=None, ssl=False,
               ssl_ca_certs=None, ssl_certfile=None, ssl_keyfile=None) -> redis.Redis
```

- 不變量：**所有新參數預設為 None/False 時，回傳值與現狀 byte-identical**
  （`redis.Redis(host, port, db, decode_responses=True)`，不帶任何 ssl/auth kwargs）。
- `ssl=True` 時才把 `ssl=True, ssl_ca_certs, ssl_certfile, ssl_keyfile` 傳入 `redis.Redis`；
  `password`/`username` 為 None 時不傳（避免改變 AUTH 行為）。
- `decode_responses=True` 永遠保留。
- 🔴 **所有權**：這是**唯一**允許建構 runtime `redis.Redis` 的函式。`stargazer/server.py` 與
  `operator_panel/server.py` 原本各自在 `run()` 內直建 `redis_lib.Redis(...)`，**必須改為呼叫
  `make_redis(**kwargs)`**。`tests/conftest.py` 的直建為測試 fixture，不在此限。

### 2. `resolve_redis_kwargs()` + bus profile（新增於 core/conn.py）

```python
DEFAULT_PROFILE_PATH = os.path.expanduser("~/.aether/config.json")

def load_bus_profile(path=DEFAULT_PROFILE_PATH) -> dict
    # 讀 JSON；檔不存在 / 壞掉 → 回 {}（永不 raise，永不阻斷 localhost）

def resolve_redis_kwargs(cli: dict | None = None, *, env=os.environ,
                         profile: dict | None = "AUTO") -> dict
    # 回傳 make_redis 的 kwargs（host/port/db/password/username/ssl/ssl_ca_certs/...）
```

- 🔴 **profile 預設自動載入**：`profile` 預設為哨兵 `"AUTO"`，此時**內部呼叫 `load_bus_profile()`**
  讀 `~/.aether/config.json`。所有 runtime 呼叫端只寫 `resolve_redis_kwargs(cli=...)` 即自動吃 profile
  （否則 `bus use` 落盤的端點不會被任何指令使用）。僅單元測試傳入明確 dict（或 `profile={}`）來隔離。
- 🔴 **Precedence（每個欄位獨立套用）**：`cli（非 None）> env（AETHER_REDIS_*）> profile > 內建預設`。
- 🔴 **profile 不得覆蓋 env**：env 層永遠優先於 profile（保護 docker-compose 容器內由
  stargazer / operator 服務 `environment` 注入的 `AETHER_REDIS_HOST: "redis"`）。
- 🔴 **密碼來源**：`password` 預設**只由 cli/env 提供，不從 profile 讀**（避免 `~/.aether` 明文存密碼）。
  `username`/`ssl`/`ssl_ca_certs` 可由 profile 提供。
- env 對應：`AETHER_REDIS_HOST/PORT/DB/PASSWORD/USERNAME/TLS（"1"/"true"）/TLS_CA`。
- profile schema（MVP 單一 profile，保留 `name` 欄向後相容）：
  ```json
  { "name": "default", "host": "...", "port": 6380, "db": 0,
    "ssl": true, "ssl_ca_certs": "/abs/ca.pem", "username": null }
  ```

### 3. `Registry` — additive 註冊 + duplicate-id fail-closed（core/registry.py）

```python
class DuplicateBodyError(Exception): ...   # 新增

def Registry.register_body(self, body: Body, *, force=False) -> str
    # 回傳 "added" | "unchanged" | "forced"；衝突且未 force → raise DuplicateBodyError

def Registry.sync(self, bodies, *, prune=False, force=False) -> None
    # prune=False（新預設）：additive merge，逐 body 套 register_body 語意，不刪別人
    # prune=True：舊行為（delete-all 後重建），僅限單一擁有者完整重置
```

- 🔴 **`register_body` 必須 ATOMIC（audit 高）**：「先 HGET 判斷再 HSET」非原子，兩台主機可同時讀到
  「不存在」而 last-write-wins。實作用 **`WATCH REGISTRY_KEY` → 比對 `HGET` → `MULTI`/`HSET`/`EXEC`**
  的樂觀 CAS（`WatchError` 重試），或等效 Lua script，確保 compare-and-set 原子。
- 🔴 **`sync` 預設改為 additive（`prune=False`）**：移除無條件 `delete(REGISTRY_KEY)`。
- 🔴 **duplicate-id fail-closed**：「id 已存在且**內容不同**」→ 預設 raise `DuplicateBodyError`
  （避免兩台主機用同 id → 共用 `aether:inbox:<id>` / `grp-<id>` 造成 split-brain）。內容**相同**→
  "unchanged"（冪等 re-register OK）。`force=True` → 覆寫。
- 🔴 **註冊策略改「只註冊自己的 body」**：`cmd_client_setup` 與 `run_observatory.main()` 不再
  `load_and_sync` 整份檔案（會 re-register 別人的 body 而撞 fail-closed），改為 `register_body(<本機
  那個 body>)`。`mcp_server.AetherBridge.__init__` 仍 `load_and_sync`（additive；互動 session 需要看
  到完整本機星圖），但本機 constellation 應只含自己的 body。三處呼叫點清單見「改動檔案」。
- `add()`（既有單 hset，無守門）**保留不變**，heartbeat 每 tick 重加自己 body 仍走 `add`（必須恆成功）。

### 4. CLI：`bus use` / `register`（cli.py）

```
aether bus use --host H --port P [--db N] [--tls] [--tls-ca PATH] [--username U]
    # ping（用解析後的連線參數，密碼取自 AETHER_REDIS_PASSWORD）通過後，才寫 ~/.aether/config.json
    # （只寫 host/port/db/ssl/ssl_ca_certs/username，不寫密碼）。ping 失敗 → 不落盤、rc=1。

aether register [--host H --port P ...] [--id ID] [--force]
    # = (若給 --host) bus use 的連線+落盤 + client setup 的 body 註冊（register_body, additive）。
    # 原子性：先用記憶體中的連線參數 ping 成功，才落盤 profile，再註冊 body。
    # 完成後印： → aether observatory <id>（不自動啟動 Observatory）。
```

- 既有 `--redis-host/--redis-port/--redis-db` 與新 `--redis-password/--redis-tls-ca/
  --redis-username` 旗標：argparse 預設改為 **`None`**（tri-state），交給 `resolve_redis_kwargs`
  套 precedence。TLS 用**一對互斥旗標**：`--redis-tls`（`store_const True`）/ `--redis-no-tls`
  （`store_const False`），共用 `dest=redis_tls, default=None` —— 讓 CLI 既能開也能**強制關**
  （覆蓋 env/profile 的 true），補掉「只能開不能關」的洞。
- 🔴 **resolve 要在所有「使用點」之前，不只 `_make_redis`（audit 高）**：`cmd_mcp_setup` 會把
  `args.redis_db/host/port` 寫進 `.mcp.json` 的 env（`build_mcp_server_entry` 內 `str(redis_db)`）。
  若預設改 None 直接傳 → 寫出 `"None"`，破壞既有測試（斷言 `AETHER_REDIS_DB=="0"`）。故 `cmd_mcp_setup`
  **先 `resolve_redis_kwargs` 取得實際 db/host/port 再傳入**。
- 🔴 **所有權**：所有 CLI 子指令與 passthrough（observatory/send/consult/mcp serve）連線時，
  一律 `make_redis(**resolve_redis_kwargs(cli=<argparse 來的 dict>))`；不得各自直建。

### 5. Framework / 既有行為備註

- `redis-py` ≥5：`ssl=True` 時 `ssl_ca_certs` 指 CA PEM；TLS over IP 需 **server 憑證含該 IP 的
  SAN**，否則 client 驗證失敗。
- 雙埠：Redis 7 原生 TLS（`--tls-port 6380 --port 6379` 並列）。

---

## 改動檔案

| 檔案 | 改動描述 |
|---|---|
| `aether/core/aether_client.py` | `make_redis` 加 password/username/ssl/ssl_ca_certs/ssl_certfile/ssl_keyfile（預設不改行為） |
| `aether/core/conn.py`（新增） | `load_bus_profile` + `resolve_redis_kwargs`（precedence flag>env>profile>default；密碼不入 profile） |
| `aether/core/registry.py` | 新增 `DuplicateBodyError`、`register_body`；`sync` 改 additive 預設（`prune` opt-in）+ 衝突 fail-closed |
| `aether/cli.py` | 連線旗標改 None tri-state + 新增 tls/password/username 旗標；`_make_redis` 走 resolver；新增 `bus use`、`register` 子指令；`cmd_client_setup` 改用 `register_body` |
| `aether/run_observatory.py` | 連線走 resolver；**null/不存在 working_dir → hard error**；消費迴圈加 reconnect/backoff + 重連後 `recover_pending`；`--heartbeat-ttl` 旗標 |
| `aether/mcp_server.py` | 連線旗標 + 走 resolver（`main()` parse_args + make_redis）；`AetherBridge.__init__` 的 `except Exception: pass` 改 log warning + 更新過時註解（sync 已非 delete-all） |
| `aether/tests/conftest.py` | 兩個 redis fixture 加 `password=os.environ.get("AETHER_REDIS_PASSWORD") or None`（auth bus 下測試仍可連；無密碼時不變）(audit N1) |
| `aether/send_message.py`、`aether/consult.py` | 連線旗標 + 走 resolver |
| `aether/stargazer/server.py` | `run()` 改呼叫 `make_redis(**resolve_redis_kwargs(...))`，**移除 `run()` 內直建 `redis_lib.Redis`** |
| `aether/operator_panel/server.py` | 同上（`run()` 內直建改走 `make_redis`） |
| `aether/docker-compose.yml` | redis：`requirepass ${AETHER_REDIS_PASSWORD}` + TLS（6380）；**6379 改綁 `127.0.0.1`**；掛載 certs；redis 服務 `env_file: .env`；**healthcheck 改帶 `-a "$AETHER_REDIS_PASSWORD" --no-auth-warning`**（否則 NOAUTH 永遠 unhealthy）；**stargazer 與 operator 兩服務都加 `env_file: .env`**（目前只有 operator 有 → stargazer 連不上有密碼的 bus） |
| `aether/.env.example` | 加 `AETHER_REDIS_PASSWORD`、`AETHER_REDIS_TLS*` 範本 |
| `aether/scripts/make-certs.sh`（新增） | openssl 產自簽 CA + server cert（含 IP SAN），印到期日 |
| `aether/.gitignore` / root `.gitignore` | 忽略 `aether/certs/`、`~/.aether` 不在 repo（僅文件提醒） |
| `aether/tests/test_cross_machine.py`（新增） | F1/F2/F3 + 盲區的單元測試（見驗收） |
| `README.md` / `aether/README.md` | 補「跨機部署（hub-and-spoke + TLS + register）」一節（keep-readme-updated） |
| `aether/docs/DEVLOG.md` | 每步實作紀錄 |

呼叫端（不需邏輯改、但受連線重構影響）：`demo_*.py` 維持 `make_redis(db=...)` 即可（localhost）。

### Registry `sync`/`load_and_sync` 呼叫點清單（audit 要求逐一定性）

| 呼叫點 | 現況 | 改動後動作 |
|---|---|---|
| `run_observatory.main()` | `load_and_sync(file)` | → `register_body(<本機 body>)`（只註冊自己，additive、fail-closed） |
| `cli.cmd_client_setup` | `load_and_sync(file)` | → `register_body(<本機 body>)`（同上；捕捉 `DuplicateBodyError`） |
| `mcp_server.AetherBridge.__init__` | `if not registry.all(): load_and_sync(file)`（empty-guard，`except Exception: pass`） | → 保留 empty-guard（僅 bus 空時 seed → 不會撞 DuplicateBodyError）；`load_and_sync` 現 additive；**把 silent `except Exception: pass` 改為 log warning（不吞錯）**；更新已過時註解（sync 已非 delete-all）(audit N2) |
| `demo_register._setup`、`stargazer/demo_feed._setup`、`stargazer/live_capture` | `sync(...)`（**無 flushdb**） | → `sync(..., prune=True)`（要乾淨種子） |
| `demo_phase2`、`demo_phase4` | `flushdb()` + `sync(...)` | → `sync(..., prune=True)`（已 flush，明示語意；行為等價） |
| `tests/conftest`、`test_p3_scenario_4` | `sync(...)`（flushed test db） | → **不改**（flushed → additive 結果同 prune） |

---

## 實作步驟

### Step 1 — `make_redis` 加連線參數（aether_client.py）
1. 擴充簽名（見介面合約 §1），新增 keyword-only 參數，預設 None/False。
2. 組 kwargs：僅當值非 None/非 False 時才放入傳給 `redis.Redis` 的 dict；`decode_responses=True` 恆在。
3. 不改任何既有呼叫端（位置參數 host/port/db 不動）。

### Step 2 — `core/conn.py`（新模組）
1. `load_bus_profile(path)`：`json.load`；`FileNotFoundError`/`JSONDecodeError`/`OSError` → 回 `{}`。
2. `resolve_redis_kwargs(cli, env, profile)`：逐欄位 `cli[k] if cli.get(k) is not None else
   env.get(AETHER_REDIS_<K>) if present else profile.get(k) if 非密碼欄 else default`。
   - bool 欄（ssl）：env `"1"/"true"/"yes"` → True。
   - 密碼欄：`cli > env`，**跳過 profile**。
3. 回傳只含「最終非預設」需要的 kwargs（host/port/db 一律帶；其餘有值才帶）。

### Step 3 — Registry additive + 守門（registry.py）
1. 定義 `DuplicateBodyError`（含 id、existing、incoming 摘要的訊息）。
2. `register_body(body, force=False)` — **原子 compare-and-set**：
   ```
   with redis.pipeline() as pipe:           # 樂觀鎖
     while True:
       try:
         pipe.watch(REGISTRY_KEY)
         existing = pipe.hget(REGISTRY_KEY, id)   # watch 後仍可讀
         if existing == body.to_json(): pipe.unwatch(); return "unchanged"
         if existing is not None and not force:
             pipe.unwatch(); raise DuplicateBodyError(id, existing, body)
         pipe.multi(); pipe.hset(REGISTRY_KEY, id, body.to_json()); pipe.execute()
         return "added" if existing is None else "forced"
       except WatchError: continue            # 有人在我們讀後改了 → 重試
   ```
   （`redis-py` 的 `WatchError` 來自 `redis.exceptions`；fakeredis/真 redis 皆支援 pipeline+watch。）
3. `sync(bodies, prune=False, force=False)`：
   - `prune=True` → 維持舊：`delete(REGISTRY_KEY)` 後逐一 hset。
   - `prune=False`（預設）→ 逐 body 呼 `register_body(b, force=force)`；任一 raise 直接往外拋
     （fail-closed，不吞）。
4. `load_and_sync(path, prune=False, force=False)`：傳遞參數給 `sync`。
5. **不動 `add`**（heartbeat 重加自己用，必須恆成功、無守門）。

### Step 4 — 連線重構：所有入口走 resolver
1. `cli.py`：`_add_redis_opts` 把三個 redis 旗標預設改 `None`，新增 `--redis-password`、
   `--redis-tls`/`--redis-no-tls`（互斥 store_const True/False，`dest=redis_tls`,`default=None`）、
   `--redis-tls-ca`、`--redis-username`；`_make_redis(args)` →
   `make_redis(**resolve_redis_kwargs(cli=_cli_dict(args)))`。
2. **`cmd_mcp_setup` 先 resolve**：取 `kw=resolve_redis_kwargs(cli=_cli_dict(args))`，把 `kw['db']`、
   （非 localhost 時）`kw['host']/kw['port']` 傳入 `build_mcp_server_entry` 與顯示路徑——**不可**直接傳
   `args.redis_db`（None → 寫出 `"None"`，破壞既有測試）。
3. `run_observatory.py`、`send_message.py`、`consult.py`、`mcp_server.py`：同樣旗標改 None +
   `make_redis(**resolve_redis_kwargs(cli=...))`。
4. `stargazer/server.py` 的 `run()`、`operator_panel/server.py` 的 `run()`：刪除直建
   `redis_lib.Redis(...)`，改 `raw = make_redis(**resolve_redis_kwargs(cli={...由 run() 參數/env...}))`；
   其餘（ReadOnlyRedis 包裝 / token）不動。

### Step 5 — `bus use` / `register`（cli.py）
1. `cmd_bus_use(args)`：`kwargs=resolve_redis_kwargs(cli=...)`；`make_redis(**kwargs).ping()`；成功才
   把 `{name:"default", host, port, db, ssl, ssl_ca_certs, username}`（**不含 password**）寫
   `~/.aether/config.json`（`os.makedirs(~/.aether, exist_ok=True)`，檔 `chmod 600`）。ping 失敗 rc=1。
2. `cmd_register(args)`：若給 `--host` 先做 bus use 的 ping+落盤（原子：ping 過才落盤）；再跑
   `cmd_client_setup` 等效邏輯（append constellation + `register_body(local_body, force=args.force)`）；
   末印 `→ aether observatory <id>`。**不** spawn Observatory。
3. `cmd_client_setup`：把 `Registry(r).load_and_sync(CONSTELLATION_PATH)` **改為
   `Registry(r).register_body(<本機這個 body>, force=args.force)`**（只註冊自己，不 re-register 別人）；
   捕捉 `DuplicateBodyError` → 印清楚錯誤 + 提示 `--force`，rc=1。`--force` 旗標加到 client setup。
4. `build_parser`：加 `bus`(sub: `use`) 與 `register` 子指令；`register` 為一般子指令（非 passthrough）。

### Step 6 — Observatory 守門 + reconnect（run_observatory.py）
0. 註冊改「只註冊自己」：把 `Registry(redis).load_and_sync(args.constellation)` **改為
   `Registry(redis).register_body(cfg)`**（`cfg` = 本 project 的 Body）；捕捉 `DuplicateBodyError`
   → `sys.exit`（明確訊息：本機 constellation 與 bus 上同 id 內容衝突，請修正或 `--force`）。
1. working_dir 守門（取代現行只 warn）：
   - `cfg.working_dir is None` → `sys.exit(f"'{project_id}' 的 working_dir 為 null（標記為遠端/非本機 body），不應在此啟動 Observatory")`。
   - `working_dir 非 None 但 not isdir` → `sys.exit(...)`（從 WARNING 升為 error）。
2. reconnect/backoff：把 `while True` 內主體包進 `try/except (redis.exceptions.ConnectionError,
   redis.exceptions.TimeoutError, redis.exceptions.AuthenticationError)`；指數退避（如 1→2→4→…→30s 上限），
   重連後先 `obs.recover_pending()` 再續迴圈。`AuthenticationError` 視為致命 → 印明確訊息後 `sys.exit`。
3. 加 `--heartbeat-ttl`（預設 30，不改舊預設）。

### Step 7 — docker-compose + certs（F1 基礎建設）
1. `make-certs.sh`：openssl 產 CA + server cert，`-addext "subjectAltName=IP:<BUS_IP>,DNS:localhost"`，
   印 `notAfter` 到期日；輸出到 `aether/certs/`（gitignored）。
2. compose redis 服務：
   - command 加 `--requirepass "${AETHER_REDIS_PASSWORD:-}"` 與（TLS 開時）`--tls-port 6380 --port 6379
     --tls-cert-file ... --tls-key-file ... --tls-ca-cert-file ...`。
   - 🔴 **空密碼 = 不啟用 auth**：`AETHER_REDIS_PASSWORD` 未設時 `requirepass ""` 等同關閉認證 →
     localhost dev（含測試）連線 **byte-identical**；設了密碼才開 auth。
   - ports：`"127.0.0.1:6379:6379"`（**loopback only**）+ `"6380:6380"`（對外 TLS）。
   - 掛載 `./certs:/certs:ro`；`env_file: .env`。
   - 🔴 **healthcheck 兩種模式都要綠**：`["CMD-SHELL", "if [ -n \"$AETHER_REDIS_PASSWORD\" ]; then
     redis-cli -a \"$AETHER_REDIS_PASSWORD\" --no-auth-warning ping; else redis-cli ping; fi | grep -q PONG"]`
     （有密碼帶 `-a`，無密碼直接 ping；否則 requirepass 後 `redis-cli ping` 回 NOAUTH → service 永遠
     unhealthy → 依賴它的 stargazer/operator 起不來）。
3. 🔴 **stargazer 與 operator 服務都加 `env_file: .env`**（目前只有 operator 有）：否則 stargazer
   拿不到 `AETHER_REDIS_PASSWORD`，連不上有密碼的 bus。
4. `.env.example` 補 `AETHER_REDIS_PASSWORD=`、`AETHER_REDIS_TLS=`、`AETHER_REDIS_TLS_CA=`。

### Step 7b — 測試 fixture 支援 auth（audit N1，高）
1. `tests/conftest.py` 的兩個 `redis_lib.Redis(...)`（`_redis_available` 與 `r`）加
   `password=os.environ.get("AETHER_REDIS_PASSWORD") or None`（必要時 `ssl`/`ssl_ca_certs` 同理）。
2. 結果：未設密碼 → 行為與現狀完全一致；開發者若在本機跑了 requirepass 的 bus，設好
   `AETHER_REDIS_PASSWORD` 後**測試仍連得上**（AC1 全套在兩種模式都綠）。conftest 因此**從「不改動」
   移出**，列為本 task 改動。

### Step 8 — 測試 + 文件
1. `tests/test_cross_machine.py`（見驗收 Agent 必做）。
2. 兩份 README 補跨機節；DEVLOG 每步一條。

---

## 失敗路徑

- **連線失敗**（壞密碼/TLS 不符/不可達）：`make_redis().ping()` raise → 各 entry point `sys.exit`
  附「檢查 host/port/password/TLS/CA」訊息。`run_observatory` 在**運行中**斷線 → reconnect/backoff；
  `AuthenticationError` → 致命退出（不無限重試壞密碼）。
- **duplicate-id**：`register_body`/additive `sync` raise `DuplicateBodyError` → CLI 捕捉印
  「id X 已被註冊且內容不同；用 `--force` 覆寫或改用別的 id」rc=1；Observatory 啟動時若 raise → 退出。
- **profile 壞掉**：`load_bus_profile` 吞掉例外回 `{}`（不可阻斷 localhost）。
- **null/不存在 working_dir**：`run_observatory` **hard error 退出**（不在錯目錄跑 claude）。
- **bus use ping 失敗**：不落盤 profile（避免半完成污染），rc=1。
- **register 原子性**：ping 失敗 → 不落盤、不註冊 body；ping 過但 body 註冊 raise → profile 已落盤
  （可接受：端點正確，body 衝突由使用者 `--force`/改 id 解決，重跑 register 冪等）。

---

## 不改動的部分

- `AetherClient` 的 emit/consumer-group/hold/mirror 邏輯、`envelope`、`guards`、`processing_log`、
  `control`、`session_store`、Observatory 的 §13/§14/§17/§18 行為。
- Stargazer 唯讀不變量（`ReadOnlyRedis` facade）、operator localhost+token、recipient guard。
- `Registry.add`、heartbeat 重加自己 body 的路徑。
- 除上述改動檔案外，其他檔案均不改動。
  （註：`tests/conftest.py` 因 audit N1 需加 password 直通，已移入改動檔案，非「不改動」。）

### Non-goals（行為層）
- 本 task **不**做 Redis HA / clustering / failover。
- 本 task **不**做公網滲透防護 / NAT 穿透（建議 VPN/tunnel，僅文件化）。
- 本 task **不**實作 Redis ACL per-identity users（只預留 `username` 參數形狀）。
- 本 task **不**做 mTLS client 憑證流程（只留 `ssl_certfile/ssl_keyfile` pass-through 參數）。
- 本 task **不**新增 `hello`/discovery 公告（移出 MVP）。
- 本 task **不**改 heartbeat 預設 TTL（只加可調旗標 + 文件建議）。
- 本 task **不**改 client 端密碼儲存為 profile（密碼走 env；profile 不存密碼）。

---

## 驗收標準

### Agent 必做（可機器執行）

```bash
cd /Users/abnertsai/JiaBao/grady/orrery-aether

# AC1 無 env/無參數 → make_redis 行為不變（不帶 ssl/auth kwargs）+ 全套測試綠
python3 -c "import inspect,aether.core.aether_client as a; s=inspect.signature(a.make_redis); \
  assert 'password' in s.parameters and 'ssl' in s.parameters and 'ssl_ca_certs' in s.parameters; \
  assert s.parameters['password'].default is None and s.parameters['ssl'].default is False; print('AC1 sig ok')"
python3 -m pytest aether/tests -q          # 既有 88 + 新增，全綠

# AC2 直建 redis 入口已歸一：唯一合法建構在 make_redis（aether_client.py），測試 fixture 例外；
#     stargazer/operator/其他 runtime 入口不得再直建 → 計數必須為 0
test "$(grep -rn -E 'redis_lib\.Redis\(|redis\.Redis\(' --include='*.py' aether \
  | grep -v __pycache__ | grep -vE 'aether/core/aether_client.py|tests/conftest.py' | wc -l | tr -d ' ')" = "0" \
  && echo 'AC2 no stray redis builders'

# AC3 precedence：profile 不蓋 env
python3 -c "from aether.core.conn import resolve_redis_kwargs as r; \
  k=r(cli={'host':None}, env={'AETHER_REDIS_HOST':'envhost'}, profile={'host':'profhost'}); \
  assert k['host']=='envhost', k; \
  k2=r(cli={'host':None}, env={}, profile={'host':'profhost'}); assert k2['host']=='profhost', k2; \
  k3=r(cli={'host':'clihost'}, env={'AETHER_REDIS_HOST':'envhost'}, profile={}); assert k3['host']=='clihost'; \
  print('AC3 precedence ok')"

# AC4 密碼不從 profile 讀
python3 -c "from aether.core.conn import resolve_redis_kwargs as r; \
  k=r(cli=None, env={}, profile={'password':'fromprofile'}); \
  assert k.get('password') in (None,''), k; print('AC4 password not from profile')"

# AC5 sync additive：註冊 B 不刪 A（用 test db；見 test_cross_machine.py）
python3 -m pytest aether/tests/test_cross_machine.py -q -k "additive or duplicate or working_dir or precedence or tls_sig"

# AC6 compose：6379 綁 loopback、舊裸埠消失、6380 TLS、requirepass、healthcheck 帶 -a、
#     redis+stargazer+operator 三服務都有 env_file
python3 -c "import yaml; c=yaml.safe_load(open('aether/docker-compose.yml')); s=c['services']; \
  ports=s['redis']['ports']; \
  assert any(str(p).startswith('127.0.0.1:6379') for p in ports), ports; \
  assert not any(str(p)=='6379:6379' for p in ports), ports; \
  assert any('6380' in str(p) for p in ports), ports; \
  cmd=' '.join(s['redis']['command']) if isinstance(s['redis']['command'],list) else str(s['redis']['command']); \
  assert 'requirepass' in cmd, cmd; \
  hc=' '.join(s['redis']['healthcheck']['test']); assert '-a' in hc and 'AETHER_REDIS_PASSWORD' in hc, hc; \
  assert '.env' in str(s['stargazer'].get('env_file')), s['stargazer']; \
  assert '.env' in str(s['operator'].get('env_file')), s['operator']; \
  print('AC6 compose ok')"

# AC7 register/bus use 子指令存在 + bus use 落盤行為（單元測試 test_bus_use_persists_profile）
python3 -c "from aether import cli; p=cli.build_parser(); p.parse_args(['register']); print('AC7 register parses')"
python3 aether/cli.py bus use --help >/dev/null && echo 'AC7 bus use ok'

# AC8 mcp setup 仍寫 AETHER_REDIS_DB="0"（None-default 回歸守門）+ profile 自動載入：見單元測試
python3 -m pytest aether/tests/test_cli.py -q -k "mcp_setup" && echo 'AC8 mcp setup db regression ok'
```

新測試（`tests/test_cross_machine.py`，除非另註明）至少涵蓋：
- `test_make_redis_no_args_byte_identical` — **monkeypatch `redis.Redis`**，斷言 `make_redis()` 無參數
  時傳入的 kwargs 僅 `host/port/db/decode_responses`（**不含** ssl/password/username）。
- `test_make_redis_ssl_passthrough` — `make_redis(ssl=True, ssl_ca_certs=..., password=...)` 時 kwargs
  正確帶入（同樣 monkeypatch）。
- `test_resolve_precedence_flag_env_profile_default` + `test_password_never_from_profile`。
- `test_resolve_profile_autoloaded`（`profile` 不傳時，monkeypatch `load_bus_profile` 被呼叫且其值生效）。
- `test_sync_additive_does_not_delete_others`（registry 有 A，`sync({B})` → A 仍在）。
- `test_sync_prune_deletes_missing_bodies`（`sync({B}, prune=True)` → A 不在）。
- `test_register_body_duplicate_fail_closed`（同 id 不同內容 → `DuplicateBodyError`；`force=True` → 覆寫；
  同內容 → "unchanged"）。
- `test_register_body_cas_retries_on_watcherror`（monkeypatch pipeline 模擬一次 `WatchError` → 重試後成功）。
- `test_observatory_null_working_dir_hard_errors`（選定 body `working_dir=None` → `SystemExit`）。
- `test_cli.py::test_mcp_setup_writes_stable_identity_and_merges`（**既有，必須仍綠**）— None-default 回歸：
  `AETHER_REDIS_DB` 仍為 `"0"`。
- `test_bus_use_persists_profile`（`tmp_path` 當 HOME；ping monkeypatch 成功 → 寫出 profile 且**不含
  password**；ping 失敗 → 不落盤、rc=1）。
- `test_cli.py::test_mcp_setup_claude_cli_resolves_db`（`--method claude-cli` 路徑也用 resolved db；
  monkeypatch `shutil.which`+`subprocess.call`，斷言組出的 `-e AETHER_REDIS_DB=0`，非 `"None"`）(audit N3)。

### Human 補做（需要人類介入）
- [ ] 真實兩機（或一機雙 db + TLS）跑 `make-certs.sh` → A 起 TLS Redis → B `aether register --host <A IP> --port 6380 --tls --tls-ca ca.pem`（`AETHER_REDIS_PASSWORD` 設好）→ `aether observatory <id>` → 從另一端 ask，確認 B 收到並回覆（端到端 over TLS+auth）。
- [ ] 確認 server cert 含 IP SAN：`openssl s_client -connect <A IP>:6380` 驗證無 hostname mismatch。
- [ ] 確認 6379 在 A 上只 loopback：從外部主機 `redis-cli -h <A IP> -p 6379 ping` 應**連不到**。
- [ ] 故意用錯密碼啟 Observatory → 應致命退出且訊息清楚（非無限重試）。
- [ ] 拔網路模擬斷線 → Observatory 應退避重連、重連後續跑（PEL 不丟）。

---

## 已知限制

- 共享密碼 = 同信任域內**無 sender 認證**：拿到密碼的任何主機都能對任何 inbox `xadd`；recipient
  guard 只擋「回覆給未登錄 to」，擋不住「冒充 from_」。跨信任域需 Redis ACL（本 task 只預留 `username`）。
- **cert 到期 = 全網同時斷線**且無預警；`make-certs` 印到期日，輪替須人工。CA 建議長效期、server cert 較短。
- 兩套 secret store：bus 機（A）容器讀 `.env`（讀不到 `~/.aether`），client 機（B/C）連線參數讀
  `~/.aether/config.json` + 密碼走 env；輪替須兩邊同步（文件說明）。
- 跨機 heartbeat TTL 預設 30s 不變；高延遲鏈路建議 `--heartbeat-ttl` 調大（「> 2× 最壞 RTT + beat interval」）。
- 跨機部署要求**每台主機的本機 constellation 只列自己的 body**（registry-as-truth）；否則 additive
  註冊會在 id 衝突時 fail-closed，須 `--force` 或修正本機 constellation。預設 `aether/constellation.yaml`
  同時含 `event_storming_tool` 與 `genesis`，跨機新主機**須先精簡成只留自己的 body**。
- **MCP 互動 sender 跨遠端安全 bus（次要、部分延後）**：`mcp_server.py` 走 resolver（env+profile），但
  Claude Code 只把 `.mcp.json` 的 env 區塊傳給 MCP server。連線端點/TLS 可由 `~/.aether` profile 提供；
  **密碼**在 project-scope（會進版控）`.mcp.json` 不可放 → 跨機 MCP sender 請用 `--scope local`
  （gitignored）放 `AETHER_REDIS_PASSWORD`，或讓該機 profile + 啟動環境提供。完整跨機 MCP secret 流程
  列為後續。
- 依賴：無前置 task；建立在現有單機 Aether（88 測試綠）之上。
