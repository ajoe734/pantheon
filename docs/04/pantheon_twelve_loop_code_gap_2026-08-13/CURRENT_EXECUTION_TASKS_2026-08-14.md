# Pantheon 十二循環 Current Execution Tasks

日期：2026-08-14

來源：`CURRENT_GAP_2026-08-14.md`

狀態：catalog frozen；16/16 canonical materialized；supervisor dispatch underway

## 去重結果

- 舊 `L12-MFC-R4-*` 18-task catalog 已全數 terminal，不能重送；本 catalog 只補最新
  程式碼仍存在的缺口。
- 目前 canonical nonterminal work 只有 `AGORA-FE-CANDIDATE-20260813`、
  `AGORA-FE-INTEGRATION-20260813`、`AGORA-HOSTED-ACCEPTANCE-20260813`。本 catalog
  不修改它們的 frontend Agora paths 或 hosted verifier/evidence paths。
- Open Pantheon PR 只有本 GAP 文件 PR #4889 與不相關的 archive PR #4837；沒有其他
  product implementation PR 可接手本 scope。
- `docker-compose.yml` 只由一個 integration task 修改，避免多個 component worker 衝突。
- 每個 owner/reviewer 都由 Claude 與 Antigravity 兩個 provider family 交叉配置；owner
  與 reviewer 不相同。Supervisor 可依實際 auth/quota 做 governed reassignment。

## 16-task DAG

| Wave | Task | Repo | Owner | Reviewer | Depends on |
|---|---|---|---|---|---|
| W1 | `L12-CURRENT-ALPHA-ADMISSION-20260814` | pantheon | Claude | Antigravity | — |
| W1 | `L12-CURRENT-TEACHING-IDENTITY-20260814` | pantheon | Antigravity | Claude | — |
| W1 | `L12-CURRENT-CONSULTATION-WIRING-20260814` | pantheon | Claude2 | Antigravity2 | — |
| W1 | `L12-CURRENT-DEPLOYMENT-AUTH-20260814` | pantheon | Antigravity2 | Claude2 | — |
| W1 | `L12-CURRENT-AGORA-HANDOFF-CUTOVER-20260814` | pantheon | Claude | Antigravity2 | — |
| W1 | `L12-CURRENT-CAPITAL-ARTIFACT-20260814` | pantheon | Claude2 | Antigravity | — |
| W1 | `L12-CURRENT-BFF-TRUTH-20260814` | pantheon | Antigravity2 | Claude | — |
| W1 | `L12-CURRENT-FE-TRUTH-20260814` | execute-plans | Antigravity | Claude | — |
| W2 | `L12-CURRENT-IMITATION-HTTP-20260814` | pantheon | Antigravity | Claude2 | Agora cutover |
| W3 | `L12-CURRENT-COMPOSE-INTEGRATION-20260814` | pantheon | Claude | Antigravity | Teaching, Consultation, Deployment, Agora, Imitation, Capital, BFF |
| W4 | `L12-CURRENT-E2E-RESEARCH-20260814` | pantheon | Claude2 | Antigravity2 | Alpha, Teaching, Compose |
| W4 | `L12-CURRENT-E2E-HUMAN-LEARNING-20260814` | pantheon | Antigravity2 | Claude2 | Consultation, Agora, Imitation, Compose |
| W4 | `L12-CURRENT-E2E-RUNTIME-20260814` | pantheon | Claude | Antigravity | Deployment, Capital, BFF, Compose |
| W5 | `L12-CURRENT-CROSS-LOOP-E2E-20260814` | pantheon | Antigravity | Claude | three E2E groups, FE truth |
| W6 | `L12-CURRENT-LEGACY-RETIRE-20260814` | pantheon | Claude2 | Antigravity2 | cross-loop E2E |
| W7 | `L12-CURRENT-HOSTED-ACCEPT-20260814` | pantheon | Antigravity2 | Claude2 | cross-loop E2E, legacy retire, existing Agora hosted acceptance |

完整 objective、scope、out-of-scope、acceptance、validation、rollout 與 rollback 位於
`execution-tasks-current-2026-08-14.json`，該 JSON 是 materialization 的唯一 task-spec
來源。

## 執行規則

1. W1 八個 tasks 可立即平行；Imitation 只等待 Agora durable intake。
2. Compose task 是唯一 `docker-compose.yml` writer，並負責 Source default-on 與 Evolution
   token wiring；不建立另一 scheduler、dispatcher、monitor 或 bridge。
3. 三個 W4 E2E tasks 使用不同測試檔，可同時執行。失敗只產生 run report，不自動新增
   repair task。
4. Legacy retirement 必須等待 replacement E2E 全綠，確保刪的是舊 mechanism，不是再加
   compatibility layer。
5. Hosted acceptance 必須讀取 exact deployed FE/BFF identities；不得以 merged PR、container
   alive、fixture 或 task completion 代替產品閉環。

## Materialization evidence

2026-08-14T07:36:59Z 已透過 command runtime
`768eba39b35d4e9c53beaef22fe7bf841b8f5e45` 的官方
`scripts/human-ops-status.sh assign`，將 catalog 的 16/16 tasks 寫入 authoritative
task-state。這是 AGENTS.md 允許的 canonical Human/Ops task command，不是手改 JSON，也
不是 chatbox subagent。

- catalog SHA-256：`4119f3b2759279182f3e131b46d9cef6ae733907ae61dbe6320f91307f259a33`
- canonical readback：16 筆全數存在、generation 1、catalog digest 相同，初始狀態均為
  `todo`；owner 各為 Claude、Claude2、Antigravity、Antigravity2 四筆。
- supervisor readback：2026-08-14T07:38:37Z 已建立五個 isolated-worktree auto-worker
  runs，Antigravity 兩筆、Antigravity2 兩筆、Claude 一筆，且五筆 canonical task 已轉為
  `in_progress`。其餘 tasks 依 provider capacity 與 declared DAG 留在 `todo` 等待 supervisor，
  沒有由 chatbox 手動啟動。
- machine-readable receipt：
  `materialization-receipt-current-2026-08-14.json`。

先前兩次 dev bridge 嘗試均為 **0 canonical rows**，不是本次 materialization truth：

1. `pkt-l12-current-clean-closure-20260814-v1` 在 live supervisor 缺少
   `BRIDGE_SIGNING_PUBLIC_KEYS_JSON` 時被 verifier 拒絕。
2. `pkt-l12-current-clean-closure-20260814-v2` 通過 signature verification，但因缺少由獨立
   operator authority 發出的真實 MFA canonical-mutation receipt，被
   source/operator-separation gate 拒絕。沒有偽造 authorization，也沒有再造 v3 packet。

這兩點是 supervisor/dev-bridge mechanism gap，未混入 12 循環產品 execution tasks。後續
若要修 bridge，應另出機制盤點與 task；不得靠改寫本 catalog 或 product task 繞過。

本節只接受 processed canonical readback 與 supervisor worker receipt。Queue file 本身不
代表 materialized，worker process 也只代表 implementation underway，不代表 12 循環完成。
