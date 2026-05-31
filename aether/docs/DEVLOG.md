# Aether DEVLOG

每個邏輯變更一條：what / why / 決策依據。對應 git commit。

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
