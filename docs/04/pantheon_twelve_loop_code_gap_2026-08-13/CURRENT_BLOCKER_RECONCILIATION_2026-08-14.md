# Pantheon 十二循環 Current Blocker Reconciliation

日期：2026-08-14

範圍：只解除目前阻斷 W3 Compose 的 component/review gates；不啟動後段 E2E、不改
supervisor 機制、不新增平行產品 mechanism。

## 判定

原 16-task catalog 已完成 materialization，且 Alpha、Deployment、Agora、Capital、BFF 五筆
已合併 `dev`。目前阻斷不是新的產品需求，而是三筆未形成合法 delivery 與一筆 closeout
evidence 未完成。

| Existing task | Current evidence | Canonical action | Product action |
|---|---|---|---|
| `L12-CURRENT-TEACHING-IDENTITY-20260814` | `review`；沒有 task branch/PR；宣告的 current test 不存在；static bearer 尚未證明可通過 Teaching JWT authority | Human/Ops reopen 原 task，保留原 ID/scope/owner/reviewer | owner 補 JWT、401 degraded health、current test、PR/checks/review/merge |
| `L12-CURRENT-FE-TRUTH-20260814` | `review`；`execute-plans` 沒有 task PR | Human/Ops reopen 原 task | owner 從最新 `execute-plans/dev` 建 branch，交付同 scope PR |
| `L12-CURRENT-IMITATION-HTTP-20260814` | local anchor `41c2501c...` 未 push；包含未宣告 `main.py`；in-scope HTTP client/direct-store removal 尚未形成 PR | Human/Ops reopen 原 task；不得交付 out-of-scope `main.py` | owner 只交付原三個 artifacts；Research HTTP/readback failure 必須 fail closed |
| `L12-CURRENT-CONSULTATION-WIRING-20260814` | PR #4893 head `f9fedaf5...` 已有 code/tests，但 evidence 仍為 owner-ready/pending independent review | Human/Ops reopen；owner/reviewer 改為 Antigravity2/Claude 以完成 reviewer-owned evidence cut | Antigravity2 只補 verdict/checksum，Claude review exact new head，然後 merge |

## Imitation 漏 scope 修正

`services/policy-learning/main.py` 不在既有 Imitation task 的 immutable artifact guard，不能偷偷
加入原 PR，也不能改寫既有 canonical contract。唯一新增的 execution task 是
`L12-CURRENT-IMITATION-ENTRYPOINT-20260814`，scope 只有 `main.py` 與專用測試；它修正 worker
settlement fail-open，不建立第二個 Research client、queue 或 handoff。

既有 `L12-CURRENT-IMITATION-HTTP-20260814` 在 supplemental task 合併前不得 approve。如此
下游仍只以原 Imitation task 為 gate，不需要 supersede 原 ID，也不需要重建後段 DAG。

Machine-readable task spec：
`execution-task-current-imitation-entrypoint-2026-08-14.json`。

## Closeout 順序

1. Materialize supplemental Imitation entrypoint task。
2. Canonical reopen Teaching、FE、Imitation。
3. Canonical reopen/reassign Consultation 給 Antigravity2/Claude；只完成 PR #4893 evidence
   closeout。
4. Supervisor 正常派原 owners 與 supplemental owner；chatbox 不直接實作產品 code。
5. 四個原 gates 全部 `done` 後才允許 Compose。若 implementation/E2E 失敗，只回報 gap，
   不自動建立 repair task。

## Out of scope

- supervisor lifecycle／review dedupe 修復
- dev bridge 修復
- 新資安、HA、壓測、live capital
- Compose、per-loop E2E、cross-loop E2E、hosted acceptance 本輪執行
