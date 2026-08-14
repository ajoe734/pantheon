# Pantheon 十二循環 Current Execution Tasks

日期：2026-08-14

來源：`CURRENT_GAP_2026-08-14.md`

狀態：catalog frozen for governed materialization；receipt 待補

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

本節只接受：signed packet ID/digest、processed supervisor receipt、canonical task IDs 與
readback hash。Queue file 本身不代表 materialized，worker process 本身也不代表完成。
