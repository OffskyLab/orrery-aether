# Aether 統一 CLI（aether mcp setup / client setup / 「/」叫用 MCP）

## 來源

討論：`aether/docs/discussions/2026-05-31-aether-cli.md`（status: consensus，2 輪）
計畫：`aether/docs/cli/00-plan.md`
決策依據：討論檔 C1–C10 + Round 2「最終定案」/「決策紀錄」。

## 目標

把目前分散的 Aether 腳本（`run_observatory.py` / `send_message.py` / `consult.py` /
`mcp_server.py` / `stargazer.server` / `operator_panel.server`）統一到一個 `aether`
CLI，讓使用者能 (1) `aether mcp setup` 在當前專案設好 MCP server（其能力＋「/」prompts
出現在 Claude Code）、(2) `aether client setup` 把專案登錄成 bus 上的 Body 並驗證連到
Redis、(3) 在 Claude Code 用 `/mcp__aether__<prompt>` 直接叫用能力。**不重寫 Phase 1–4
既有邏輯、不破壞現有 65 個通過的測試**。

---

## 介面合約（Interface Contract）

### 1. `aether/cli.py`（新增）— dispatcher

- **模組頂端必須先 bootstrap import root**（在 import 任何 `aether.*` submodule 之前）：
  `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`。
  理由：複用現有 4 支腳本的同一 pattern（`consult.py` 等），讓 `python3 <abs>/aether/cli.py`
  可從任一 cwd 執行（C1）。**順序所有權**：sys.path bootstrap 由 `cli.py` 模組層負責，
  各子指令 handler 不再各自 insert。
- `def main(argv: Optional[list[str]] = None) -> int`：解析 argv（預設 `sys.argv[1:]`），
  dispatch 到子指令 handler，回傳 exit code（0 成功、非 0 失敗）。`if __name__ == "__main__": sys.exit(main())`。
- 子指令（MVP）：`mcp setup`、`client setup`、`server status`、`observatory <id>`、
  `send ...`、`consult ...`、`who`、`mcp serve ...`。
- 共用 parent parser 提供 `--redis-host/--redis-port/--redis-db`，預設讀
  `AETHER_REDIS_HOST/PORT/DB`（沿用現有腳本 env 慣例，行為不變）。
- alias 子指令（`observatory`/`send`/`consult`/`mcp serve`）以 `argv` 轉呼叫對應腳本的
  `main(argv)`，不複製邏輯。
- `install-shim [--dir <path>]` 子指令：把一支 `aether` shim（`#!/bin/sh` + `exec <python_exe>
  <abs cli.py> "$@"`，`python_exe`=`sys.executable`）寫到 `--dir`（預設 `~/.local/bin`，可被 PATH 涵蓋），
  使 **bare `aether <cmd>` 成為真實指令**——這不是 pip packaging（無 pyproject/`__init__`），只是一行 shim，
  正是 C1 定案（延後 packaging、用 shim 達成 global 指令）。印出「請確認 `<dir>` 在 PATH 上」。

### 2. 各腳本 `main()` → `main(argv=None)`（修改）

對 `run_observatory.py`、`send_message.py`、`consult.py`、`mcp_server.py` 各自的 `main()`：
- 簽名改 `def main(argv: Optional[list[str]] = None) -> Optional[int]`。
- 內部 `ap.parse_args()` → `ap.parse_args(argv)`。
- 保留 `if __name__ == "__main__": main()`（直接執行 fallback 不變）。
- **不變量**：不帶 argv 呼叫時行為與現狀 100% 一致（argv=None → argparse 讀 sys.argv）。

### 3. `aether/cli_support.py`（新增）— 純函式（無 I/O 副作用，便於單元測試）

- `def sanitize_id(name: str) -> str`：lowercase、非 `[a-z0-9_]` → `_`、去頭尾 `_`。
  用於由 cwd basename 推導 project id。
- `def merge_mcp_config(existing: dict, server_name: str, server_entry: dict) -> dict`：
  回傳新 dict；在 `existing["mcpServers"]` 下 upsert `server_name`，**保留其他 server 與
  任何既有 key（如 `_comment`）**；`existing` 無 `mcpServers` 時建立之；不可 mutate 入參。
  冪等：對同輸入重複套用結果相同。（C3）**形狀驗證**：若 `existing` 非 dict 或
  `existing["mcpServers"]` 存在但非 dict → raise `ValueError`（呼叫端不寫檔，見失敗路徑，Codex LOW）。
- `def build_mcp_server_entry(python_exe: str, server_script_abs: str, identity: str,
  redis_db: int = 0, constellation_abs: Optional[str] = None, extra_env: Optional[dict] = None) -> dict`：
  回傳 `{"type":"stdio","command":<python_exe>,"args":[<server_script_abs>,"--identity",<identity>],
  "env":{"AETHER_REDIS_DB":str(redis_db), ...}}`。`python_exe` 必須是**絕對 python 路徑**
  （呼叫端傳 `sys.executable`，C 跨切面 action item a）；非預設 redis host/port 才寫入 env。
- `def append_constellation_body(existing_text: str, body_id: str, fields: dict) -> tuple[str, str]`：
  **append-only**（C7）。回傳 `(new_text, action)`，`action ∈ {"appended","exists"}`。
  - 若 `body_id` 已存在於 `bodies:` 下（以 `yaml.safe_load` 判斷）→ 回 `(existing_text, "exists")`，不改動。
  - 否則**用 `yaml.safe_dump({body_id: fields}, allow_unicode=True, sort_keys=False)` 渲染該 body 區塊**
    （safe_dump 會正確跳脫含引號/冒號/特殊字元的 description，避免 `load_constellation()` 失敗——Codex MED），
    再以正確縮排 append 到原 text 末（`bodies:` 下層），**逐字保留既有所有行（含 header 註解）**，
    回 `(new_text, "appended")`。**不可**用手刻字串模板拼 YAML。
  - `fields` 至少含 `description: str`、`capabilities: list[str]`、`inbox: str`、`working_dir: str`。
- `def infer_metadata(project_dir: str) -> dict`：回傳**建議值** `{"description": str, "capabilities": list[str]}`。
  - description：讀 `CLAUDE.md`→`README.md` 首個非空段落（截斷至合理長度）；找不到 → `""`（**不發明**，C6）。
  - capabilities：由 manifest 偵測語言／框架（`package.json`→`["react","typescript"]` 視內容、
    `Package.swift`→`["swift"]`、`pyproject.toml`/`setup.py`→`["python"]`），找不到 → `[]`。

### 4. `aether/mcp_server.py` `build_server`（修改）— 新增 `@mcp.prompt()`（C5）

於 `build_server(bridge)` 函式內、與既有 `@mcp.tool()` 同層新增 prompts，
回傳 `str`（注入為訊息）。**reads = render 時呼叫現成同步 `AetherBridge` method 並把結果格式化
回傳；writes = 注入最小單一 tool-call 指令、render 時不做寫入、不注入輪詢迴圈。**

| prompt（`/mcp__aether__…`） | 型別 | 行為 |
|---|---|---|
| `who()` | read | render 時呼叫 `bridge.list_bodies()`，格式化成可讀清單回傳。 |
| `transcript(thread: str)` | read | render 時呼叫 `bridge.transcript(thread)`，**payload 須 bounded**（每 hop text 截斷至上限、總長設上限）回傳。 |
| `ask(to: str, question: str, thread: str = "")` | write | 回傳最小指令字串：「呼叫 `aether_ask(to, question[, thread])`，回報它回傳的 thread，並提示我用 `/mcp__aether__poll <thread>` 取回覆。**不要自行輪詢迴圈、不要重送。**」**render 時不呼叫 `bridge.ask`**（避免 double-send，C5/Codex）。 |
| `poll(thread: str)` | read | render 時呼叫 `bridge.poll(thread)` 格式化回傳。 |
| `discuss(from_project: str, to_project: str, topic: str)` | write | 回傳最小指令：「呼叫 `aether_discuss(...)`，回報 thread，提示我用 `/mcp__aether__transcript <thread>` 觀看。」render 時不呼叫 `bridge.discuss`。 |
| `stop(thread: str)` | write/confirm | **confirm-first**（C5）：回傳「若要終止 thread `<thread>`，請呼叫 `aether_control('<thread>','terminate')`」——render 時**不** terminate。 |

- 既有 6 個 `@mcp.tool()` **不改**（仍是模型自呼的 async 介面）。一個 server 同時有 tools + prompts（已驗證）。

### 4b. `AetherBridge` 心跳重註冊（修改，修 Codex HIGH）

`AetherBridge.start_heartbeat` 的迴圈每 tick 除了 `heartbeat.beat(identity)` 外，**也要
`registry.add(self 的 transient Body)`**（冪等）。理由：`client setup` 或任一 Observatory 啟動會呼叫
`Registry.sync()`，其 `delete(REGISTRY_KEY)` 會**清掉 transient `<project>-mcp` identity**，導致對它的
回覆被 `_deliver_reply` 以 `invalid_recipient` 拒絕。每 tick 重 add 可在 sync 後數秒內自我修復。

### 5. `aether mcp setup` handler（cli.py 內，呼叫 cli_support）

- 簽名（語意）：偵測 cwd → `project_id = sanitize_id(basename(cwd))` 或 `--id`；
  `identity = f"{project_id}-mcp"`（**穩定，不可 fall through 到 random uuid**，C4/Gemini）。
- `--scope {project,local,user}` 預設 `project`（C2）；`--method {mcp-json,claude-cli}` 預設 `mcp-json`（C3）。
- 寫入：`mcp-json` 法 → 讀 cwd `.mcp.json`（無則 `{}`）→ `merge_mcp_config(...)` → 寫回；
  entry 用 `build_mcp_server_entry(sys.executable, <abs mcp_server.py>, identity, redis_db, <abs constellation>)`。
  `claude-cli` 法 → `claude mcp add aether --scope <scope> -e AETHER_REDIS_DB=<db> [-e AETHER_REDIS_HOST=..
  -e AETHER_REDIS_PORT=.. -e AETHER_CONSTELLATION=<abs>] -- <python_exe> <abs script> --identity <id>`
  （**須帶過 redis/constellation env，不可只傳 `--identity`**——Codex MED；偵測 `claude` 在 PATH，
  否則 fallback mcp-json 並提示）。
- 收尾印出：approve + `/mcp` 指引、slash 形如 `/mcp__aether__<prompt>`、以及**「star chart 可能為空，
  請跑 `aether client setup` 或啟動任一 Observatory 來 populate」警告**（C8/Gemini DX trap）。
- project scope 印**「絕對路徑會寫進 repo」警告**並提示 `--scope local`（C2）。

### 6. `aether client setup` handler

- `project_id = sanitize_id(basename(cwd))` 或 `--id`；`working_dir = cwd`（絕對）。
- metadata：`infer_metadata(cwd)` 取建議值 → 互動確認（可編輯）；`--description/--capability/--yes`
  非互動跳過（C6）。找不到來源時以空值起手，不發明。
- 寫入 constellation（package 內 `<abs>/aether/constellation.yaml`）：
  `append_constellation_body(text, project_id, fields)`；`action == "exists"` 時印「已存在，未改」。
- 連線驗證：`make_redis(...).ping()`；失敗印「Redis 不可達」+ 啟動指令（不自動起，C9）。成功後可選
  `Registry(redis).load_and_sync(constellation)` 發佈星表。
- 收尾 nudge：指向 `aether mcp setup`（outbound）與 `aether observatory <id>`（上線收訊，預設唯讀工具）。

### 7. `aether server status` handler

- `make_redis(...).ping()` 回報 up/down；列出 registry 中各 Body 與 `Heartbeat.is_online` 狀態。
- **不**含 `up/down`（MVP 範圍外，C9）。

---

## 改動檔案

| 檔案路徑 | 改動描述 |
|---|---|
| `aether/cli.py` | 新增：argparse dispatcher + sys.path bootstrap + 子指令 handler（mcp setup / client setup / server status / who / install-shim / 及 alias）。 |
| `aether/bin/aether` | 新增：薄 shim（`exec <sys.executable> <abs cli.py> "$@"`），由 `install-shim` 寫到 PATH dir，使 bare `aether` 可用（非 packaging，C1）。 |
| `aether/cli_support.py` | 新增：純函式 `sanitize_id` / `merge_mcp_config` / `build_mcp_server_entry` / `append_constellation_body` / `infer_metadata`。 |
| `aether/mcp_server.py` | 修改：`build_server` 新增 6 個 `@mcp.prompt()`；`main()` → `main(argv=None)`。既有 tools 不動。 |
| `aether/run_observatory.py` | 修改：`main()` → `main(argv=None)`，`parse_args(argv)`。 |
| `aether/send_message.py` | 修改：同上。 |
| `aether/consult.py` | 修改：同上。 |
| `aether/tests/test_cli.py` | 新增：dispatcher 路由 + handler 單元測試（mock handler）。 |
| `aether/tests/test_cli_support.py` | 新增：純函式測試（merge 冪等、append-only、infer、sanitize）。 |
| `aether/tests/test_mcp_prompts.py` | 新增：`build_server` prompts 測試（reads prefetch 內容、writes 為指令不觸發 bridge 寫入）。 |
| `aether/README.md` / 根 `README.md` | 修改：補 `aether` CLI 用法（依 keep-readme-updated 規則）。 |
| `.gitignore`（repo root，新增）；`aether/docs/DEVLOG.md`（新增） | Phase 0 變更紀錄基建（C10）。 |

呼叫端（call sites）：現有 `python3 aether/<script>.py ...`、`python3 -m aether.stargazer.server`、
`demo_*.py`、tests **不受影響**（main(argv=None) 向後相容）。

---

## 實作步驟

### Phase 0 — 變更紀錄基建（先行，C10）
1. repo root `git init`；新增 `.gitignore`：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.DS_Store`、
   `aether-redis-data`、以及 `--scope local` 產生的 `.mcp.json`（避免絕對路徑入庫）。
2. baseline commit 現狀（65 測試綠）。新增 `aether/docs/DEVLOG.md`（每邏輯變更一條：what/why/決策）。
3. 之後每個 Phase 至少一個 commit + 一條 DEVLOG。

### Step 1 — `cli_support.py` 純函式（先寫、先測）
1. `sanitize_id`：regex `[^a-z0-9_]+` → `_`，strip `_`。
2. `merge_mcp_config(existing, name, entry)`：`copy.deepcopy(existing)`；`setdefault("mcpServers",{})`；
   `["mcpServers"][name]=entry`；回傳（不 mutate 入參）。
3. `build_mcp_server_entry`：依介面合約組 dict；非預設 host/port 才加入 env key。
4. `append_constellation_body(text, body_id, fields)`：
   - `safe_load(text)` 取 `bodies` map 判斷 `body_id` 是否存在 → 存在回 `(text,"exists")`。
   - 否則 render 一段（固定縮排 2 空格）body 區塊字串：
     ```
     <pad>{body_id}:
     <pad>  description: "{description}"
     <pad>  capabilities: [{cap1, cap2, ...}]
     <pad>  inbox: "aether:inbox:{body_id}"
     <pad>  working_dir: "{working_dir}"
     ```
     append 到 text 末（確保前面有換行），回 `(new_text,"appended")`。**逐字保留原 text**。
5. `infer_metadata`：依序試 `CLAUDE.md`/`README.md` 首段；manifest 偵測語言；無則空。

### Step 2 — `cli.py` dispatcher（非破壞）
1. 模組頂端 sys.path bootstrap（**在任何 `from aether...` import 之前**）。
2. 建 root parser + subparsers；parent parser 帶 redis 選項。
3. alias handler：`observatory/send/consult` → `import` 對應模組、呼叫 `main(remaining_argv)`。
   `mcp serve` → `mcp_server.main(remaining_argv)`。
4. `who` handler：`Registry(redis).all()` + `Heartbeat.is_online`，印表。
5. `server status` handler：`redis.ping()` + 線上 Body 列表。
6. `install-shim` handler：渲染 `#!/bin/sh\nexec "<sys.executable>" "<abs cli.py>" "$@"\n` 寫到 `--dir`
   （預設 `~/.local/bin/aether`）、`chmod +x`、印「確認 `<dir>` 在 PATH」。
7. `main(argv=None)` 回 exit code。

### Step 3 — 各腳本 argv refactor
1. 四支腳本 `def main(argv=None)` + `parse_args(argv)`；保留 `__main__` guard。
2. **立即重跑全測試**，確認 65 綠（C 跨切面：test gate 綁每階段）。commit。

### Step 4 — `aether mcp setup`
1. handler：推導 project_id/identity（`<project>-mcp`）；組 entry（`sys.executable` 絕對路徑）。
2. `mcp-json` 法：read-merge-write `.mcp.json`（用 `merge_mcp_config`）。`claude-cli` 法：shell-out。
3. 印 approve/`/mcp`/slash 名稱/空 chart 警告/project-scope 絕對路徑警告。commit。

### Step 5 — `aether client setup`
1. handler：infer→確認 metadata（或 `--yes`/flags）；`append_constellation_body` 寫 constellation。
2. `redis.ping()`；失敗印啟動指令；成功可 `load_and_sync`。
3. 印 nudge（mcp setup / observatory）。commit。

### Step 6 — 「/」prompts + 心跳重註冊
1. `build_server` 內加 6 個 `@mcp.prompt()`（依介面合約表）。reads 呼叫 bridge method；
   writes 回指令字串（**不呼叫 bridge 寫入**）；`transcript` bounded；`stop` confirm-first。
2. `AetherBridge.start_heartbeat` 迴圈每 tick 加 `registry.add(self Body)`（修 Codex HIGH：sync 後自我修復）。
3. 加 `tests/test_mcp_prompts.py`。手動 live 驗證（Claude Code `/mcp`、slash 叫用）。commit。

### Step 7 — 文件
1. 更新兩份 README 的 CLI 用法段。commit。

---

## 失敗路徑

- **Redis 不可達**：`make_redis().ping()` raise（redis exception）→ handler catch → 印「Redis 不可達 +
  `docker compose -f <abs>/aether/docker-compose.yml up -d redis`」→ 回 exit code 1（不自動起，C9）。
- **未知 project / body**（`mcp serve`/`who`/`client setup` 對不存在 id）→ 印 known 列表 + exit 1。
- **`.mcp.json` 既有檔非合法 JSON / 形狀錯誤**（如 `mcpServers` 為 list）→ 讀取 raise `json.JSONDecodeError`
  或 `merge_mcp_config` raise `ValueError` → 印「`.mcp.json` 格式錯誤，未改動」+ exit 1（**不覆蓋/不寫檔**）。
- **constellation description 含特殊字元** → `append_constellation_body` 以 `yaml.safe_dump` 渲染、正確跳脫，
  不會產生破壞 `load_constellation()` 的 YAML。
- **`claude` 不在 PATH（--method claude-cli）** → 偵測不到 → fallback `mcp-json` 並印提示（可恢復）。
- **constellation body 已存在** → `append_constellation_body` 回 `"exists"` → 印「已存在，未改」+ exit 0（非錯誤）。
- **prompt render 例外**（read prompt 呼叫 bridge 失敗）→ prompt 回傳一段說明字串（不可 crash MCP server）。
- **infer 找不到來源** → 回空值（非錯誤），由互動確認補。

---

## 不改動的部分

- Phase 1–4 核心：`core/`、`observatory/main.py` 的 pipeline、`stargazer/`、`operator_panel/`
  邏輯**不改**（除腳本 main 簽名與 mcp_server prompts）。
- 既有 6 個 `@mcp.tool()` 簽名與行為不改。
- `constellation.yaml` 既有 body 與 header 註解逐字保留（append-only）。
- 既有 `python3 aether/<script>.py` / `-m` 呼叫方式維持可用。

### Non-goals（行為層）
- 本 task 不包含 **pip packaging**（`pyproject.toml` / `console_scripts` / `aether/__init__.py`）—延後 Phase 5（C1）。
  （MVP **有**提供一支薄 `aether` shim（`install-shim`）讓 bare 指令可用——shim ≠ packaging。）
- 本 task 不包含 `aether server up/down`（只 `status`）—延後、explicit、docker-gated（C9）。
- 本 task 不包含 `aether init` 合併指令（兩 setup 獨立，C8）。
- 本 task 不包含 `ruamel.yaml` 或 constellation 既有 body 的 edit/remove（append-only only，C7）。
- 本 task 不改變既有 tools 的 async 語意，也不在 prompt render 時送訊息/terminate（C5）。
- 本 task 不新增 `dashboard`/`panel` 子指令（延後）。

---

## 驗收標準

### Agent 必做（可機器執行）

```bash
cd /Users/abnertsai/JiaBao/grady/orrery-aether/aether

# 1. 既有 65 測試不破壞（argv refactor 後）
python3 -m pytest -p no:cacheprovider -q   # 期望：65 passed, 2 skipped

# 2. 新檔存在且可 import
python3 -c "import sys; sys.path.insert(0,'..'); import aether.cli, aether.cli_support; print('cli ok')"

# 3. dispatcher 可跑（-m、絕對路徑、別的 cwd 三種入口）
python3 -m aether.cli --help        # 從 repo root
python3 aether/cli.py --help         # 從 repo root
( cd /tmp && python3 /Users/abnertsai/JiaBao/grady/orrery-aether/aether/cli.py --help )  # 別的 cwd 以絕對路徑（Codex LOW：須機器檢查）

# 4. 純函式單元測試（merge 冪等、append-only、sanitize、infer）
python3 -m pytest -p no:cacheprovider -q tests/test_cli_support.py

# 5. CLI 路由測試
python3 -m pytest -p no:cacheprovider -q tests/test_cli.py

# 6. MCP prompts 測試：6 個 prompt 註冊；ask/discuss/stop 為「指令字串」不觸發 bridge 寫入
python3 -m pytest -p no:cacheprovider -q tests/test_mcp_prompts.py

# 7. 關鍵 symbol 存在（語意錨點，非計數）
grep -q "def merge_mcp_config" cli_support.py
grep -q "def append_constellation_body" cli_support.py
grep -q "def main(argv" cli.py
grep -q "@mcp.prompt()" mcp_server.py
grep -q "sys.executable" cli.py          # mcp setup 寫絕對 python 路徑
# ask prompt 不在 render 時呼叫 bridge.ask（writes 無 render 寫入）：
python3 -m pytest -p no:cacheprovider -q tests/test_mcp_prompts.py -k "ask_is_instruction_only"

# 8. mcp setup 產生的 .mcp.json 為合法 JSON、保留其他 server、含穩定 <project>-mcp identity
python3 -m pytest -p no:cacheprovider -q tests/test_cli.py -k "mcp_setup"
```

### Human 補做（需要人類介入）

- [ ] 在某真實專案 `aether mcp setup` → 開/重啟 Claude Code → `/mcp` 顯示 `aether` 已連線並可核准。
- [ ] 在 Claude Code 打 `/` → 看到 `/mcp__aether__who`、`/mcp__aether__ask` 等出現於選單。
- [ ] `/mcp__aether__who` → 直接看到 body 清單（read prefetch，零模型 tool call）。
- [ ] `/mcp__aether__ask genesis "..."` → 模型呼叫 `aether_ask` 一次、回報 thread、提示 `/poll`（無自動輪詢迴圈、無重送）。
- [ ] `aether client setup` 在某專案 → constellation 新增該 body、header 註解逐字保留、重跑 `aether who` 可見。
- [ ] `aether server status` 正確回報 Redis up/down 與線上 Body。
- [ ] `aether install-shim` 後（`~/.local/bin` 在 PATH），`aether mcp setup` 以 bare 指令可跑。
- [ ] 跑 `client setup`（會 sync registry）後，`<project>-mcp` identity 仍能在數秒內收到回覆（心跳重註冊生效）。

---

## 已知限制

- **packaging 延後（C1）**：MVP 無 bare `aether` 指令；以 `python3 -m aether.cli` 或絕對路徑
  `python3 <abs>/aether/cli.py` 執行，使用者可自行裝薄 shim 上 PATH。pyproject + console_scripts 列 Phase 5。
- **constellation 僅 append（C7）**：可新增 body；edit/remove 既有 body 不在範圍（要做須 full re-dump
  並會丟註解，另案）。
- **poll dedup 為 in-memory（跨切面 c）**：MCP server 重啟（`/mcp` reconnect）會 reset `_returned`，
  `/mcp__aether__poll` 可能重現已看過的回覆。spec 註明，非 blocker。
- **project scope 絕對路徑入庫（C2）**：`.mcp.json` 含 machine-specific 絕對 python/script 路徑；
  跨機分享須改 `--scope local` 或手動調整。
- **prompt 注入非硬保證（C5/fixed premise）**：write prompts 注入「呼叫某 tool」指令，最終仍依模型遵循；
  設計上保持指令短、單一路徑、可由 tool 的 idempotency 兜底。
- 依賴：無前置 task；需 Redis 可達（測試用 db15，沿用現有 fixture）。
