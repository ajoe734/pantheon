# OPENCLAW-CRON-WRITE-SCOPE

## 一句話
讓 **creation-time 的 BFF→adapter persona OODA cron 註冊路徑能真的寫入 gateway** —— 現在 adapter 這個 gateway device 只被核准「讀」scope,`cron.add`（寫）被 gateway 以 `pairing required` 擋掉。

## 背景 / 根因（已由 OPENCLAW-LIVE-WIRING live 釘死，勿重查）
- PR #2812 新增 adapter cron proxy `POST /api/openclaw-adapter/gateway/cron`（cron.* whitelist），PR #2818 加固其 JSON 解析。endpoint 已部署、**讀取（cron.list）live 正常**。
- 但透過 adapter proxy 做 **cron.add（寫）** live 失敗，實測回：
  ```
  openclaw gateway call cron.add exited 1: gateway connect failed:
  GatewayClientRequestError: scope upgrade pending approval (requestId: ...)
  GatewayTransportError: gateway closed (1008): pairing required:
  device is asking for more scopes than currently approved
  ```
- 對照：**在 gateway 容器內**直接 `openclaw gateway call cron.add`（localhost，同 device，全 scope）**成功**（backfill 已用此法灌了 5 個 job 並跑出 `cron.runs status ok`）。
- 結論：OpenClaw 2026.6.8 gateway 在 token auth 之上還有**per-device scope 核准**。adapter 作為獨立連線 device，只被核准讀，沒被核准 cron-write。
- 影響：`_try_register_persona_cron`（BFF 建 persona 時）改走 adapter 後仍然寫不進去 → 新建 persona 的 OODA cron 不會註冊。

## 要做什麼（擇一，附 live 證據）
1. **首選：核准 adapter device 的 cron-write scope** —— 查 OpenClaw gateway 的 device pairing / scope 核准機制（容器內 `openclaw gateway --help` / `/app/docs/gateway/*`；找 approve / pairing / scopes 指令或 `~/.openclaw/openclaw.json` 的 device/scope 設定），把 adapter device 一次核准為含 `cron.*` 寫。核准要**可持久化 + 可重現**（寫進 compose/init，不是手動一次性），因為 openclaw-data volume 重建或 device 重連不能又退回唯讀。
2. **次選：用一個具全 scope 的連線做寫** —— 讓 adapter 的 `gateway_cron_call` 以具寫 scope 的方式連（例如 gateway 端把 adapter 的 token/device 列入信任），或改由具 gateway 全 scope 的元件代寫。**禁止**繞回 docker-exec-from-BFF（BFF 無 docker socket，這正是原本的死路）。

## 驗收（唯一標準：live，非 mock）
1. 透過 **adapter proxy**（非 gateway 容器內）成功 `cron.add` 一個 job，回 `{"status":"ok","data":{"id":...}}`，且 `cron.list` 看得到。
2. 走**完整 BFF 路徑**：建立一個新 persona（或呼叫 reconcile endpoint），確認其 4 個 OODA cron job 真的出現在 `cron.list`（非 dry_run、非假紀錄）。
3. gateway/openclaw-data volume 重建後，scope 仍然有效（附重建後再 cron.add 成功的證據）。
4. 既有測試綠。

## 禁止
- 禁止只在有 gateway 時 skip 的 mock 測試收工。
- 禁止繞回 docker-exec-from-BFF。
- 禁止動 supervisor poll/sleep cadence。

## 相關檔 / 證據
- `services/openclaw-gateway-adapter/assistant_openclaw_provider.py`（`gateway_cron_call`）、`main.py`（`/gateway/cron` route）
- `services/control-plane/cron/persona_cron_registrar.py`（`AdapterCronRuntime`、`_get_runtime`）
- gateway 容器文件：`/app/docs/gateway/*`；device/scope 設定：`~/.openclaw/openclaw.json`
- 前置：OPENCLAW-LIVE-WIRING（PR #2812、#2818，已 merged）
