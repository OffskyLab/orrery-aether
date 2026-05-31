# Aether DEVLOG

每個邏輯變更一條：what / why / 決策依據。對應 git commit。

## 2026-05-31 — 統一 `aether` CLI

Spec: `aether/docs/tasks/2026-05-31-aether-cli.md`（status ready）。
流程：/plan → /discuss（2 輪，Codex+Gemini，共識）→ /write-spec → /audit-spec（全綠）。

### Phase 0 — 變更紀錄基建 (commit 197b5d8)
- `git init` + `.gitignore`（`__pycache__/`、`.DS_Store`、`aether-redis-data/`、local `.mcp.json`）+ baseline commit。
- **Why**: repo 原本非 git，是「記錄每次修改」最大缺口（討論 C10）。baseline 鎖住 65 測試綠的狀態，之後每變更可 bisect。
