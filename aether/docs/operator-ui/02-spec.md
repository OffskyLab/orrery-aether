# Operator 操作畫面（web UI）+ remove/unregister body

## 來源
- 計畫：`aether/docs/operator-ui/00-plan.md`
- 討論（consensus, 2 rounds, D1–D5 + rejection log；Codex 本輪未取得，audit-spec 補外部）：
  `aether/docs/operator-ui/01-discussion.md`

## 目標
operator panel 目前是 token-gated 的**純 HTTP 寫入 API、無畫面**。給它一個**自 serve 的單檔 web 操作畫面**，
涵蓋所有 operator 操作（inject / pause / resume / terminate / kill_project）**外加新的 `unregister`（從
registry 移除一個 body）**，全程維持「localhost + token、所有動作稽核、Stargazer 唯讀不變量不受影響」。

---

## 介面合約（Interface Contract）

### 1. `online_map(redis) -> dict[str, bool]`（新增於 `core/registry.py`）
- 回傳 `{project_id: is_online}`，由 `redis.hgetall(REGISTRY_KEY)` 的 key 集合 + 每個 id 的
  `Heartbeat(redis).is_online(pid)` 組成。**只讀**（`hgetall` + `exists`）→ 在 `ReadOnlyRedis` 下亦可呼叫。
- 🔴 **所有權**：心跳鍵格式由 `Heartbeat`（prefix `aether:heartbeat`）唯一擁有；此 helper 透過
  `Heartbeat` 判斷 online，不可自行硬寫鍵字串（避免與 `Heartbeat.prefix` 重複）。
- 用途：取代 Stargazer 的私有 `_online_map`，並供 operator `GET /api/bodies` 共用（不重複實作，D3）。

### 2. `OperatorService.unregister(project_id, *, force=False) -> dict`（新增於 `operator_panel/control_service.py`）
- 行為：
  1. `reg = Registry(self.client.r)`。若 **`not reg.has(project_id)`** → 直接回
     `{"project_id": project_id, "state": "absent", "removed": False}`（冪等、誠實；不謊報 removed）。
  2. 若 `Heartbeat(self.client.r).is_online(project_id)` 為真且 `force` 非真 → **raise
     `BodyOnlineError`**（在線 body 會被其 Observatory 下一輪 `register_body` 彈回，須先停它或 `force`）。
  3. 否則：`reg.remove(project_id)`（`hdel`）**＋** `Heartbeat(self.client.r).go_offline(project_id)`
     （刪 `aether:heartbeat:<id>`，免 `online_map` 殘影）**＋** `self.client.emit_operator_action(self.actor,
     "unregister", project_id=project_id)`（稽核）。
- 回傳（已移除）`{"project_id": project_id, "state": "removed", "removed": True}`。
- 🔴 不清 `aether:inbox:<id>` / `aether:hold:*` / `aether:proclog:*`（資料，可能丟未投遞訊息；超範圍）。
- 例外型別：`BodyOnlineError(Exception)`（定義在 `control_service.py`），訊息含 id 與「pass force / stop its
  Observatory」提示。`AetherClient.r` 即底層 redis（既有屬性）。

### 3. operator_panel/server.py — 新 routes（`create_operator_app(redis, token)`）
- 🔴 **關閉 FastAPI 預設 docs**：`FastAPI(title="Aether Operator", …, docs_url=None, redoc_url=None,
  openapi_url=None)`——否則 `/docs`、`/openapi.json`、`/redoc` 會**無 auth 洩漏 route schema**，與「只有
  `GET /`+`/health` 免 token」矛盾。關掉後免 token 的就只剩 `GET /` 與 `GET /health`。
- `WEB_DIR = os.path.join(os.path.dirname(__file__), "web")`（mirror Stargazer）。
- `GET /`（**無 auth**，靜態頁）：`FileResponse(WEB_DIR/index.html)`；檔不存在 → `HTMLResponse` fallback。
- `POST /unregister`（`Depends(require_token)`）：body `{project_id: str, force: bool=False}` →
  `service.unregister(project_id, force=force)`；接 `BodyOnlineError` → `HTTPException(status_code=409,
  detail=...)`。（require_token 是 dependency，**先**跑 → 無 token 即使對在線 body 也是 401，不會到 409。）
- `GET /api/bodies`（`Depends(require_token)`）：回 `[{"id","online","description","capabilities",
  "working_dir"}]`（plain dict，非 raw dataclass），由 `Registry(redis).all()` + `online_map(redis)` 組。
- `GET /api/conversations`（`Depends(require_token)`）：**明確契約**——讀 `AetherClient(redis).read_events()`
  （oldest→newest），**反向**走訪、以**首見**收集 `conversation_id`（最近在前）;**掃完所有事件後才取前 50 個**
  thread（不可在迴圈中途 break，否則 terminated-先於-message 的 thread 其 from/to 會來不及補齊）;每個 thread 的
  `from`/`to` 取其**最後一筆 `event_type=="message"`** 的 envelope from/to;`ended` = 該 thread 是否存在任一
  `event_type=="terminated"` 事件。回 `[{"conversation_id","from","to","ended"}]`（無對話 → `[]`）。
- 🔴 **所有權**：`redis`（writable）由 `create_operator_app` 持有；read endpoints 用它建 `Registry`/`online_map`/
  `AetherClient`。寫入端（inject/pause/resume/terminate/kill_project/unregister）**全部** `Depends(require_token)`，
  read endpoints（`/api/bodies`、`/api/conversations`）**亦** token-gated；免 token 的只有 `GET /` 與 `GET /health`。

### 4. `operator_panel/web/index.html`（**新檔**，單檔 SPA，vanilla HTML+JS，無 build）
- token：paste 欄位 → `sessionStorage` → 每個 fetch 帶 `Authorization: Bearer <token>`。**不**用 `?token=`。
- 區塊：token 設定；bodies 清單（`GET /api/bodies`，每列有 kill_project / unregister 按鈕）；conversations
  清單（`GET /api/conversations`，每列有 pause/resume/terminate）；inject 表單（to/intent/text/
  conversation_id?/solicit/max_hops）。
- 🔴 **XSS 防護（硬性）**：所有來自 bus 的不可信資料（body id / 描述 / 對話文字 / reason / from / to）**一律用
  `textContent` 或 DOM `createElement`/`append` 呈現，整個檔案不得出現 `innerHTML`**（避免惡意 id/文字偷走
  sessionStorage 的 token）。
- 🔴 **confirm-first**：`terminate` / `kill_project` / `unregister` 動作前需確認；`unregister`/`kill_project`
  用「打字確認」（輸入該 id 才執行）。confirm 僅 DX；真正的安全控制是 server 端 token + 稽核（直接 curl 可略過
  JS confirm）。
- 動作後顯示 server 回應（含稽核結果/錯誤），token 永不寫入 DOM/log。

### 5. `pyproject.toml`
- `[tool.setuptools.package-data]` 加 **`"aether.operator_panel" = ["web/*"]`**（否則 wheel 不含
  `operator_panel/web/index.html`，pip/pipx 裝好 `GET /` 會 404）。

### Framework 備註
- FastAPI `FileResponse` / `HTMLResponse` 已在 stargazer 用同模式（參考 `stargazer/server.py` 的 `GET /`）。
- pydantic `BaseModel` 用於 `UnregisterBody`（mirror 既有 `ConversationBody`/`ProjectBody`）。

---

## 改動檔案

| 檔案 | 改動描述 |
|---|---|
| `aether/core/registry.py` | 新增 `online_map(redis)` 共用 helper（Heartbeat-based，只讀） |
| `aether/operator_panel/control_service.py` | 新增 `BodyOnlineError` + `OperatorService.unregister`（hdel + go_offline + 稽核 + online-guard） |
| `aether/operator_panel/server.py` | 新 routes：`GET /`(FileResponse)、`POST /unregister`、`GET /api/bodies`、`GET /api/conversations`；`WEB_DIR` 常數；`UnregisterBody` model |
| `aether/operator_panel/web/index.html` | **新檔**：單檔 operator SPA（含全部操作 + 清單 + XSS-safe 呈現 + confirm-first + token 欄位） |
| `aether/stargazer/server.py` | `_online_map` 改為委派 `core.registry.online_map`（去重，行為不變） |
| `pyproject.toml` | package-data 加 `aether.operator_panel = ["web/*"]` |
| `aether/tests/test_operator_ui.py` | **新檔**：unregister(+force/+online-guard/+401)、`GET /api/bodies`(+401)、`GET /` 提供頁面、`online_map` 行為 |
| `README.md` / `README.zh-TW.md` | operator UI 一節（如何開、token、操作） |
| `aether/docs/DEVLOG.md` | 每步紀錄 |

呼叫端（不需改）：`test_p4_operator.py` 的隔離閘門（Stargazer 路由 ⊆ {GET,HEAD}）**必須仍綠**。

---

## 實作步驟

### Step 1 — `core/registry.py`：`online_map`
1. `from .heartbeat import Heartbeat`（registry→heartbeat→clock，無循環）。
2. `def online_map(redis): hb = Heartbeat(redis); return {pid: hb.is_online(pid) for pid in (redis.hgetall(REGISTRY_KEY) or {})}`。

### Step 2 — `stargazer/server.py`：委派（去重）
1. 模組頂 `from ..core.registry import REGISTRY_KEY, online_map`。
2. `_online_map(ro)` 改為 `return online_map(ro)`（`ReadOnlyRedis` 只會被呼叫 `hgetall`/`exists`，允許）。
   保留函式名與呼叫點不變（行為等價）。

### Step 3 — `control_service.py`：`unregister`
1. 定義 `class BodyOnlineError(Exception)`。
2. `unregister(self, project_id, *, force=False)`：
   ```
   from ..core.registry import Registry
   from ..core.heartbeat import Heartbeat
   reg = Registry(self.client.r); hb = Heartbeat(self.client.r)
   if not reg.has(project_id):
       return {"project_id": project_id, "state": "absent", "removed": False}   # 冪等、誠實
   if hb.is_online(project_id) and not force:
       raise BodyOnlineError(f"body '{project_id}' is online — stop its Observatory or pass force=true")
   reg.remove(project_id)
   hb.go_offline(project_id)                       # 刪 heartbeat key，免殘影
   self.client.emit_operator_action(self.actor, "unregister", project_id=project_id)
   return {"project_id": project_id, "state": "removed", "removed": True}
   ```

### Step 4 — `server.py`：routes + UI serve
1. imports：`FileResponse, HTMLResponse`；`os`；`from ..core.registry import Registry, online_map`；
   `from .control_service import OperatorService, BodyOnlineError`；`WEB_DIR`。
2. `FastAPI(title="Aether Operator", description="Authenticated control plane", docs_url=None,
   redoc_url=None, openapi_url=None)`（關 docs，見 §3）。`create_operator_app` 內保留 `redis`（closure）；
   新增上述 routes；`UnregisterBody(BaseModel)`（`project_id:str`, `force:bool=False`）。
3. `POST /unregister`：`try: return service.unregister(b.project_id, force=b.force) except BodyOnlineError as e:
   raise HTTPException(409, str(e))`。
4. `GET /api/bodies`：`om=online_map(redis); reg=Registry(redis).all(); return [{"id":pid,"online":om.get(pid,False),
   "description":body.description,"capabilities":body.capabilities,"working_dir":body.working_dir} for pid,body in reg.items()]`。
5. `GET /api/conversations`（明確契約，見 §3）：
   ```
   evs = AetherClient(redis).read_events()          # oldest→newest（已受 aether:events MAXLEN ~50000 限長）
   seen = {}                                        # conversation_id -> {from,to,ended}，插入序=最近 thread 在前
   for rec in reversed(evs):                         # newest first
       cid = rec.get("conversation_id")
       if not cid: continue
       d = seen.setdefault(cid, {"conversation_id": cid, "from": None, "to": None, "ended": False})
       if rec.get("event_type") == "terminated": d["ended"] = True
       if d["from"] is None and rec.get("event_type") == "message":
           env = rec.get("envelope") or {}; d["from"] = env.get("from"); d["to"] = env.get("to")
   return list(seen.values())[:50]                  # 取最近 50 個 thread；**掃完所有事件才切片**，避免在補齊 from/to 前就截斷（audit R2）
   ```
6. `GET /`：`FileResponse(os.path.join(WEB_DIR,"index.html"))`，不存在 → `HTMLResponse` fallback。

### Step 5 — `operator_panel/web/index.html`（單檔 SPA）
1. token 欄位 → `sessionStorage.setItem('aether_op_token', …)`；`authHeaders()` 回 `{Authorization:'Bearer '+token}`。
2. `loadBodies()`/`loadConversations()`：fetch 對應 GET（帶 token）→ 用 **`createElement`+`textContent`** 建表
   （**禁 innerHTML**）。每列附動作按鈕。
3. 動作（fetch POST 帶 token + JSON）：inject/pause/resume/terminate/kill_project/unregister。
4. confirm-first：terminate→`confirm()`；kill_project/unregister→打字確認該 id 才送。
5. 顯示回應/錯誤（textContent）；401 提示「請先設定有效 token」；409 提示「body 在線，先停 Observatory 或勾 force」。
6. 純前端、無外部 CDN/依賴。

### Step 6 — `pyproject.toml`
1. package-data 加 `"aether.operator_panel" = ["web/*"]`。

### Step 7 — 測試 + 文件
1. `tests/test_operator_ui.py`（見驗收）。
2. README ×2 + DEVLOG。

---

## 失敗路徑
- **無 token** → 既有 `require_token` raise `HTTPException(401)`（涵蓋 /unregister 與 /api/*）。
- **unregister 在線 body 且未 force** → `BodyOnlineError` → server 接成 `HTTPException(409)`；UI 顯示提示。
- **unregister 不存在的 body** → `Registry.has` 為 False → 直接回 `{"state":"absent","removed":False}`
  （冪等、誠實、**不**寫稽核）。與介面合約 §2 / Step 3 / test 一致。
- **GET / 但 web/index.html 不存在**（例如忘了打包）→ `HTMLResponse` fallback 文字（不 500）。
- **client-side confirm 被略過**（直接 curl）→ 不影響安全：endpoint 仍 token-gated + 動作仍稽核。

---

## 不改動的部分
- **Stargazer 不加任何寫入路徑**（唯讀不變量；`ReadOnlyRedis`）。僅 `_online_map` 改為委派同一個 `online_map`
  helper（純讀、行為等價），不新增寫入。
- operator 既有 `inject/pause/resume/terminate/kill_project` 行為與簽名不變（只新增 unregister + UI + reads）。
- `ControlPlane`、`AetherClient`、`Registry.remove`、`Heartbeat` 既有行為不變。
- 除上述改動檔案外，其他檔案均不改動。

### Non-goals（行為層）
- 本 task **不**改 Stargazer 的**行為/對外 API**（僅內部 `_online_map` 重構為共用 helper、行為等價）；不加
  任何寫入路徑、不加 operator 控制到 Stargazer SPA。
- 本 task **不**引入新驗證機制 / 帳號 / 角色（沿用單一 bearer token）。
- 本 task **不**把 operator 對 LAN 開放（仍 localhost + token；遠端用 SSH tunnel）。
- 本 task **不**在 unregister 清除 inbox / hold / proclog（不做資料清理）。
- 本 task **不**做 registry 的其他編輯（改描述/capabilities）——只做 remove。
- 本 task **不**把 client-side confirm 當成安全機制（它是 DX）。

---

## 驗收標準

### Agent 必做（可機器執行）
```bash
cd /Users/abnertsai/JiaBao/grady/orrery-aether

# AC1 online_map helper 存在於 core.registry
python3 -c "from aether.core.registry import online_map; import inspect; assert callable(online_map); print('AC1 ok')"

# AC2 pyproject 打包 operator_panel/web（grep，免 tomllib；Python 3.10 無 stdlib tomllib → 不可用 tomllib）
grep -Eq '"aether\.operator_panel"[[:space:]]*=[[:space:]]*\[[^]]*"web/\*"' pyproject.toml && echo 'AC2 ok'

# AC3 operator app 有新 routes 且方法正確（path+method 對，不只 path）
python3 -c "
from aether.operator_panel.server import create_operator_app
from aether.core.aether_client import make_redis
app = create_operator_app(make_redis(db=15), 'tok')
pm = {(r.path, m) for r in app.routes if hasattr(r,'path') for m in (getattr(r,'methods',None) or set())}
for need in [('/','GET'),('/unregister','POST'),('/api/bodies','GET'),('/api/conversations','GET')]:
    assert need in pm, (need, sorted(pm))
print('AC3 routes+methods ok')"

# AC4 operator SPA：存在 + 無危險 HTML sink + 有必要的安全/功能標記（避免空殼也過）
SPA=aether/operator_panel/web/index.html
test -f "$SPA" && echo 'AC4a file ok'
test "$(grep -cE 'innerHTML|outerHTML|insertAdjacentHTML|document\.write|createContextualFragment|DOMParser' "$SPA")" = "0" && echo 'AC4b no unsafe HTML sink ok'
for s in textContent sessionStorage Authorization /unregister /api/bodies /api/conversations /inject /pause /resume /terminate /kill_project confirm; do
  grep -q -- "$s" "$SPA" || { echo "AC4 MISSING marker: $s"; exit 1; }
done
echo 'AC4c required markers present'

# AC5 新測試（unregister +force/+online-guard/+401、/api/bodies +401、GET / 提供頁面）
python3 -m pytest aether/tests/test_operator_ui.py -q

# AC6 Stargazer 唯讀隔離閘門仍綠（不得因加 operator routes 而破）
python3 -m pytest aether/tests/test_p4_operator.py -q

# AC7 全套不破
python3 -m pytest aether/tests -q          # 105 + 新增，全綠
```

`tests/test_operator_ui.py` 至少涵蓋（用 `fastapi.testclient.TestClient` + 既有 `r` fixture，token 自設）：
- `test_unregister_requires_token`：`POST /unregister` 無 token → 401。
- `test_api_bodies_requires_token`：`GET /api/bodies` 無 token → 401。
- `test_api_conversations_requires_token`：`GET /api/conversations` 無 token → 401。
- `test_unregister_removes_and_audits`：先 `register_body` 一個離線 body → `POST /unregister`(token) → 200
  `state=removed`，`Registry.has` 變 False，且 `aether:events` 有一筆 `event_type=operator_action`,`action=unregister`。
- `test_unregister_absent_body`：移除不存在的 id → 200 `{state:"absent", removed:False}`,**不**寫稽核
  （absent 直接回，不 emit）。
- `test_unregister_online_body_refused_without_force`：body 有 heartbeat → 409；`force:true` → 200 removed
  且 heartbeat key 也被刪（`is_online` 變 False）。
- `test_api_bodies_shape`：register 兩個 body、一個 beat → `GET /api/bodies`(token) 回含正確 id/online/
  description 的 plain dict 清單。
- `test_api_conversations_shape`：種入 `message`(from/to) + `terminated` 事件（兩個 thread）→
  `GET /api/conversations`(token) 回正確 `conversation_id`/`from`/`to`/`ended`，最近在前，無對話 → `[]`。
- `test_get_root_serves_spa`：`GET /`（無 token）→ 200 且回傳含關鍵字（如 `Aether Operator`）。
- `test_online_map`：register 兩個 body、一個 beat → `online_map(r)` 與 `online_map(ReadOnlyRedis(r))` 皆回
  正確 `{id: True/False}`（驗證 ReadOnlyRedis 相容 + 兩種心跳狀態）。

### Human 補做（需要人類介入）
- [ ] 瀏覽器開 `http://127.0.0.1:8770/`（operator panel 在跑）→ 貼上 token → 看到 bodies/conversations 清單。
- [ ] 跑 inject / pause / resume / terminate / kill_project / unregister，確認：成功有回應、destructive 有
      confirm-first、動作出現在 Stargazer 時間軸（稽核）。
- [ ] 故意註冊一個 id 或描述含 `<img src=x onerror=alert(1)>` 之類 → UI **顯示為純文字、不執行**（XSS 防護）。
- [ ] 清掉 token 後按任一動作 → UI 顯示 401 提示、不執行。
- [ ] 對在線 body 按 unregister（不勾 force）→ 顯示 409「先停 Observatory」；勾 force → 移除。

---

## 已知限制
- **單一 bearer token、無角色**：任何持 token 者可做所有 operator 動作（含 unregister）。跨信任域/多操作員需
  日後做角色（延後）。
- **confirm-first 僅 DX**：直接打 API 可略過；真正控制是 token + 稽核。
- **unregister 不清資料**：被移除 body 的 `inbox`/`hold`/`proclog` 仍留在 Redis（避免丟未投遞訊息）；要清需另開 op。
- **online-guard 仰賴 heartbeat**：剛離線（heartbeat 尚未過期）的 body 仍算 online；要移除可 `force` 或等 TTL。
- **localhost-only**：UI/endpoint 不對 LAN；遠端管理走 SSH tunnel 到 `:8770`。
- 依賴：建立在現有 operator panel（Phase 4）+ 跨機/packaging 之上；無前置 task。
