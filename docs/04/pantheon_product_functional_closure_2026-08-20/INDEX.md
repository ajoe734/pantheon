# Pantheon 產品功能落差與實作計畫 — 2026-08-20

狀態：current-dev code/runtime re-audit；功能優先；已授權建立 governed execution tasks

這組文件取代把「十二循環」、「Agora」或「Management」分開判斷完成度的做法。產品是否完成，
必須以同一條 current-dev 使用者旅程能否真的運作、產生 durable state、由 Management 讀回，
並以 exact hosted FE/BFF identity 驗收為準。

## 文件

1. [完整產品功能 GAP](01_CURRENT_PRODUCT_FUNCTION_GAP_2026-08-20.md)
2. [重複機制、廢碼與簡化 disposition](02_CODE_DISPOSITION_AND_SIMPLIFICATION_2026-08-20.md)
3. [功能優先實作計畫](03_IMPLEMENTATION_PLAN_2026-08-20.md)
4. [Supervisor / auto-worker execution tasks](04_EXECUTION_TASKS_2026-08-20.md)
5. [Machine-readable task catalog](execution-tasks.json)

## 本輪硬邊界

- Source Ingestion **不停止開發或驗收**；dev 平時維持不對外拉資料，測試時才用 bounded
  one-shot 手動拉一次，完成後回到 `reconcile_only`。
- 先完成可運作功能、資料流、readback、UI 與 current-dev 驗收。
- 不新增資安、RBAC、MFA、token rotation、HA、合規或 hardening 工作包；既有必要控制不得拆除。
- 只做 paper execution，不啟用 live broker 或真實資金。
- `execute-plans` 是唯一前端 repo；不使用 legacy `front-ai-trading-system`，不以 Lovable publish
  作為 dev 交付。
- Supervisor、auto-worker、TaskStore 是開發工具，不是產品完成證據。

## Current baseline

| Surface | Frozen identity / observation |
|---|---|
| Pantheon source | `ajoe734/pantheon` `origin/dev` `cd93c201076f7767366a868a1b45d75a91e9317e` |
| Frontend source | `ajoe734/execute-plans` `origin/dev` `729baba8f21211074c5aa3983ecd1e79e59c8599` |
| Hosted frontend | serves FE `729baba8...`; strict live/read-only build |
| Hosted manifest BFF | declares `e50af43ab253af80ae1e0c48f9cf5448368fb6ac` |
| Actual public BFF | `/bff/version` reports `26a4fd868d73e49d5a1b37232da189d7ac9bd949` |
| Source runtime | API unhealthy; controller intentionally stopped; no continuous external pull |
| Paper runtime | producer unhealthy; 9 active bindings fail `artifact_store_missing`; fleet resource loop is excessive |
| Canonical task state | old terminal IDs are historical facts, not proof that current product journeys work |

## 判定

Pantheon 已有大量 service、API、UI、state store 與 contract tests，但目前仍不能宣稱整個產品
可用。最先要修的不是增加更多頁面，而是 source/paper runtime state 爆量、正常產品資料契約
缺口、Agora worker/producer 斷點、Management 假成功與 synthetic surface，以及 hosted identity
漂移。完整依賴與 task 分解見文件 3、4。
