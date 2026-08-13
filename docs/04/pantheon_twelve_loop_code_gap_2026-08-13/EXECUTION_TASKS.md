# Pantheon 十二循環最小功能 Execution Tasks

日期：2026-08-13

狀態：governed execution catalog；產品實作只由 supervisor-dispatched auto-worker 執行

## 1. 執行邊界

本 DAG 只處理 12 個 Pantheon 產品循環與 Management loop truth 的最小可用閉環，不加入
security hardening、HA、load/chaos、compliance、live capital、Supervisor V2 或 fleet
基礎設施工作。所有 Capital 驗證維持 paper-only。

每個 task 必須從執行當下最新 remote `dev` 建 clean worktree，建立 focused PR 到 `dev`，
完成 checks、獨立 review、merge SHA 與功能 readback。E2E failure 只輸出 GAP report，不建立
repair task、不修改 product code、不修改 canonical task state。

Live readiness 在 catalog 凍結時顯示 Antigravity 與 Antigravity2 可 auto-deliver，Claude pool
尚未 ready。因此 owner/reviewer 先使用兩個不同 Antigravity account；不得 fallback 到 Codex。
Claude 恢復 live readiness 後，supervisor 才可依正式 provider policy接手。

## 2. 最大平行 DAG

資料循環的先後不等於開發任務的先後。所有 domain tasks 都依凍結的 correlation、input/output、
receipt contract 平行開發；只有真正共享的 compose/catalog/BFF scope 集中到一個 integration
task。

```text
PLAN-FREEZE
  ├─ SOURCE      ├─ DISTILL     ├─ ALPHA       ├─ TEACH
  ├─ AGORA       ├─ IMITATION   ├─ CONSULT     ├─ DEPLOY
  ├─ CAPITAL     ├─ TELREC      ├─ EVOLUTION   ├─ BFF-HEALTH
  ├─ FE-TRUTH
  └─ VERIFIER-HARNESS

12 domain tasks ───────────────> BACKEND-TRUTH-INTEGRATE
BACKEND-TRUTH + FE-TRUTH + VERIFIER-HARNESS ──> E2E-ACCEPT
E2E-ACCEPT ──> HOSTED-CLOSE
```

Plan freeze 後有 14 筆工作可同時開始。Domain task 可以用 contract test 驗證自身 producer／
consumer adapter，但不得因另一 domain 尚未 merge 而增加鏈式 dependency。

## 3. Shared-scope 規則

- `docker-compose.yml`、`docs/deployment/loop-catalog.registry.json`、共用 loop observation adapter
  與 BFF `loop_inventory.py` 只由 `L12-MFC-R4-BACKEND-TRUTH-001` 修改。
- Domain tasks 只改自己的自然 owner；需要 compose env/activation 時，在 PR artifact 附
  `compose_delta`，由 integration task 一次套用。
- `L12-MFC-R4-FE-TRUTH-001` 只在 `ajoe734/execute-plans@dev` 工作；不得在 Pantheon checkout
  建 `execute-plans/` 子目錄。
- `L12-MFC-R4-VERIFIER-001` 只建立 verifier harness；不修 product。真正 closure run 由
  `L12-MFC-R4-E2E-ACCEPT-001` 執行。

## 4. Task inventory

| Task ID | Design | Repo | 可開始條件 | Objective |
|---|---|---|---|---|
| `L12-MFC-R4-PLAN-FREEZE-001` | Wave 0 | pantheon | 立即 | 獨立 review/merge 本 GAP、SD 與 execution catalog |
| `L12-MFC-R4-SOURCE-001` | D01 | pantheon | PLAN | 正式 source controller 與 bounded smoke 分離 |
| `L12-MFC-R4-DISTILL-001` | D02 | pantheon | PLAN | 保存 canonical Registry draft identity/readback |
| `L12-MFC-R4-ALPHA-001` | D03 | pantheon | PLAN | reviewed ReplicationAdmission 取代 seed discovery |
| `L12-MFC-R4-TEACH-001` | D04 | pantheon | PLAN | Teaching terminal eval 與 conditional ConsultRequest receipt |
| `L12-MFC-R4-AGORA-001` | D05 | pantheon | PLAN | 單一 handoff drainer、policy receipt、Agora ack |
| `L12-MFC-R4-IMITATION-001` | D06 | pantheon | PLAN | candidate handoff 到既有 Research Experiment authority |
| `L12-MFC-R4-CONSULT-001` | D07 | pantheon | PLAN | 唯一 generic workflow 與真 provider adapter |
| `L12-MFC-R4-DEPLOY-001` | D08 | pantheon | PLAN | validation-first 對齊 approval/artifact/binding identity |
| `L12-MFC-R4-CAPITAL-001` | D09 | pantheon | PLAN | default paper producer 執行 RuntimeBinding artifact |
| `L12-MFC-R4-TELREC-001` | D10 | pantheon | PLAN | validation-first runtime event→drift/incident readback |
| `L12-MFC-R4-EVOLUTION-001` | D11 | pantheon | PLAN | 共用 Evolution client 補 auth/tenant/readback |
| `L12-MFC-R4-BFF-HEALTH-001` | D12 | pantheon | PLAN | typed exact target→telemetry→incident→recovery |
| `L12-MFC-R4-BACKEND-TRUTH-001` | D13+D14 BE | pantheon | 12 domain done | 一次收斂 compose、12 observations、catalog、BFF truth |
| `L12-MFC-R4-FE-TRUTH-001` | D14 FE | execute-plans | PLAN | API error 不再變成 0 rows；strict-live 12 rows |
| `L12-MFC-R4-VERIFIER-001` | D15 harness | pantheon | PLAN | 建 non-repairing 12-case/correlated-chain verifier |
| `L12-MFC-R4-E2E-ACCEPT-001` | D15 run | pantheon | BE truth + FE + verifier | 執行真實 compose-bound closure，failure 只出報告 |
| `L12-MFC-R4-HOSTED-CLOSE-001` | Wave 6 | pantheon | E2E accept | 一次 bounded deploy 與 hosted 13-row closeout |

完整 objective、root cause、declared scope、out-of-scope、acceptance、validation、rollout、
rollback 與 required artifacts 以 `execution-tasks.json` 為準。

## 5. 舊工作去重

- 2026-07-26 舊 28-task DAG、`L12-CLOSE-001`、`L12-HOSTED-001` 與 2026-08-08
  `L12-MIN-*` 均為 historical input；不重開、不 supersede、不改舊 canonical rows。
- 舊九個 controller tasks 的有效 observation 需求改由 12 個自然 owner 與
  `BACKEND-TRUTH-001` 接手，不新增九個 controller services。
- 舊 evidence-revalidation tasks 被 domain acceptance、`VERIFIER-001` 與 `E2E-ACCEPT-001`
  接手；evidence manifest 不再是 closure proof。
- PR #4834 Agora WP-09 只作 `AGORA-001` 設計輸入；WP-10 只作 `CONSULT-001` 設計輸入。
  其餘 Agora UI、workspace、authority redesign 不在本 DAG。

## 6. Materialization evidence

Materialization 只能使用 assistant dev bridge 或 installed canonical task command；不得直接寫
queue/state JSON。完成後至少要有：canonical row、完整 metadata、supervisor observation、
auto-worker dispatch/claim（dependency-blocked row 可等待）、owner/reviewer 不同、且沒有 Codex
fallback。PR/check/review/merge/readback 屬各 task closeout artifacts，不以「row 已建立」代替。

