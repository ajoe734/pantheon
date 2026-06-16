# OPENCLAW-AGENT-TURN-LIVE-FIX

## 一句話
把「assistant → OpenClaw」與「persona OODA-loop → OpenClaw」兩條接線從**打一個不存在的 REST endpoint** 改成**走 gateway 真實支援的介面**,並以**實機 live agent turn**(非 mock)作為唯一驗收標準。

## 背景 / 根因(已由 orchestrator 實機釘死,勿重查)
- PR #1714 的 `services/openclaw-gateway-adapter/assistant_openclaw_provider.py` 把 `OPENCLAW_GATEWAY_URL` 的 `ws://` 改成 `http://`,然後 `POST /api/agents/{agent_id}/invoke`。
- **實機驗證:該 endpoint 在上游 gateway 回 404 — 它不存在。** `/api/agents`、`/api/agents/main` 全 404。
- gateway 的 agent 協定是 **WebSocket RPC**,不是 REST。官方文件(容器內 `/app/docs/gateway/remote.md`)明載:「Gateway calls the node over the Gateway WebSocket (`node.*` RPC)」;remote 設定為 `url: "ws://127.0.0.1:18789", token`。
- `#1714` 的 107 個測試全綠是因為 **HTTP transport 被 mock 掉**,從未打過真 gateway。這是「驗 wiring 形狀、沒驗 live endpoint」的假綠。
- 部署的 BFF `PANTHEON_ASSISTANT_PROVIDER` 目前仍是 `codex_cli`(沒切到 openclaw),所以線上還沒爆,但一切過去就會 404。

## Gateway 真實 API(ground truth,已驗證)
| 項目 | 值 |
|---|---|
| transport | WebSocket RPC `ws://openclaw-gateway:18789`(容器名 `pantheon-openclaw-gateway-1`,compose service `openclaw-gateway`) |
| auth | token mode;`gateway.auth.token` = `pantheon-local-token`(來源 `~/.openclaw/openclaw.json`,在 volume `pantheon_openclaw-data`) |
| 支援的程式化介面 | `openclaw agent --url ws://openclaw-gateway:18789 --token <tok> --agent main --message "<prompt>"` → stdout 即為 agent 回覆(文件 `/app/docs/tools/agent-send.md`) |
| model 金鑰 | 在 **gateway 容器** 的 `~/.openclaw/.env`(`OPENAI_API_KEY`);adapter 端**不需要**,模型在 gateway 跑 |
| 健康探針 | gateway HTTP `:18789/readyz` → `{"ready":true}`(這條是真的,可保留作 readiness) |
| 不存在的 endpoint | `POST /api/agents/main/invoke`(404) — 移除 |

驗證指令(在 gateway 容器內已證實可跑):
```
openclaw agent --agent main --message "Reply with exactly: OPENCLAW_LIVE"   # → OPENCLAW_LIVE
```

## 要做什麼

### Part A — assistant provider 走真實介面
`services/openclaw-gateway-adapter/assistant_openclaw_provider.py`:
- **不要重造輪子**:跟現有 codex/claude provider 一樣 **shell-out 到官方 `openclaw` CLI**(`openclaw agent ...`),而不是自寫 REST/WS client。
- 因此 adapter image 需安裝 `openclaw` CLI(Node 22；參考 gateway 容器 `/app`)。若 image 過重,次選:寫薄的 WS-RPC client 連 `ws://openclaw-gateway:18789` + token——但需在 PR 說明為何不能用 CLI。
- provider 設定:`--url ws://openclaw-gateway:18789 --token $OPENCLAW_GATEWAY_TOKEN --agent $OPENCLAW_AGENT_ID`。token 從環境注入(別硬寫);compose 把 `OPENCLAW_GATEWAY_TOKEN=pantheon-local-token` 給 adapter。
- readiness 仍可用 gateway `:18789/readyz`。
- 移除 `POST /api/agents/{id}/invoke` 死路。

### Part B — 確認 OODA-loop wiring 也打真 endpoint
- 檢查 `OPENCLAW-PERSONA-OODA-LOOP-WIRING`(已 review_approved)那條 persona 建立 → cron 註冊 → OpenClaw 呼叫 → OODA packet 的鏈,**OpenClaw 呼叫那一跳是否也打了同一個 404 REST 假路徑或被 mock**。
- 若是,改成 Part A 同一個真實介面(同一 provider/路徑),不要各做各的。
- `services/control-plane/cron/openclaw_client.py` 的 `OpenClawCronClient` 預設 `transport=None`/`dry_run=True`——確認 persona workflow 註冊後,實際執行路徑會走到真的 OpenClaw,不是永遠 dry_run。

### Part C — 切換 + 上線
- 部署後設 `PANTHEON_ASSISTANT_PROVIDER=openclaw`(BFF code 預設已是 openclaw,部署 env 顯式蓋成 codex_cli;把它改掉或移除覆寫)。

## 驗收(唯一標準:live,非 mock)
1. **adapter live agent turn**:部署後在 adapter 容器/或經 BFF,實際送一則 prompt,**拿到真實模型回覆**(附 PR 證據:指令 + 輸出,顯示非 mock、非 dry_run)。
2. **BFF assistant 路由 live**:`PANTHEON_ASSISTANT_PROVIDER=openclaw` 下,打 BFF 的 management-AI invoke,證明 trace 走到 gateway agent(附 gateway log 或回覆指紋)。
3. **persona OODA-loop live**:建立一個 persona 後,`/bff/ooda/packets` 計數 > 0(或 loop-runs / evolution 有真實新筆),且帶**真實 producer 指紋**(trace_id / 上游時間戳),非 fixture seed、非讀時合成。
4. 既有測試保持綠;**新增一個會真的打 deployed gateway 的 live smoke**(可 gate 在有 gateway 時才跑),補上 #1714 缺的那層。
5. PR 描述需說明:為何 #1714 的 REST 路徑是錯的、改用什麼介面、live 證據貼上。

## 禁止
- 禁止只改 mock 測試讓它綠就收工(這正是被修的 bug)。
- 禁止造假資料 / 讀時合成冒充 producer 輸出。
- 禁止動 supervisor poll/sleep cadence。

## 相關檔
- `services/openclaw-gateway-adapter/assistant_openclaw_provider.py`(#1714 新增,需修)
- `services/openclaw-gateway-adapter/main.py`(provider 註冊)
- `services/control-plane/bff/openclaw_ops_client.py:247`(`openclaw`/`openclaw_agent` 路由)
- `services/control-plane/bff/main.py`(`PANTHEON_ASSISTANT_PROVIDER` 預設 openclaw)
- `services/control-plane/cron/openclaw_client.py`(transport=None/dry_run 預設)
- gateway 容器文件:`/app/docs/gateway/remote.md`、`/app/docs/tools/agent-send.md`
