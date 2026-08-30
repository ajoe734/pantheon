# Pantheon 全產品運作 GAP、SA、SD 與平行執行凍結 — 2026-08-30

| 欄位 | 凍結值 |
|---|---|
| 文件狀態 | **待 exact-head 獨立審查之純文件規劃包** |
| Pantheon planning baseline | `origin/dev@1095c55bf42acc91fac18b701cd24ad5b1874438` |
| execute-plans baseline | `origin/dev@bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` |
| Hosted accepted pair | pair `6899d0daadb3dea2dbc3ae93456cf5818675dbd9a5c4284f676b80b5ce59c1a1`；FE `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1`；BFF `e7f010dccee33185bc260d06048f09e6d2125f28` |
| Hosted acceptance | `deploymentState=accepted`；`acceptedAt=2026-08-30T06:28:46Z`；pre/post-switch probes passed |
| 運行範圍 | Desktop、Paper/Simulation、Source dev 常態 `reconcile_only` |
| 排除 | Mobile、EP5 治理計畫、組織政策管理、real capital/live broker |
| 任務數 | 1 個 plan-freeze + 22 個 ownership-derived child tasks；另重用 1 個既有 blocked hosted task；不是配額 |
| Materialization | A=1、B=12、C=9；每批不超過 16 |

Hosted 身分來自重新讀取的 Pantheon-owned `deployment.json`。因此 OP-G03 已有 accepted exact-pair 證據，狀態是 **closed**，沒有 implementation task。未來 changed heads 的 promotion 只承擔 OP-G19/OP-G20 與一般 gate-before-switch 發布責任，不重開 OP-G03。

## 1. 本版的決策

1. **20 個 audit roots 與 remaining work 分離**：OP-G01..OP-G20 各有一筆 disposition；OP-G03 closed，18 項由本 catalog 的 active/verify tasks承擔，OP-G14 由既有 blocked AGC-14承擔。
2. **441 decorators + 421 handlers 雙重凍結**：catalog 對每個 decorator 列出 method、normalized path、handler、source line、唯一 owner task 與 target router，並對每個 source handler 列出 exactly one migration disposition。行號只作 evidence locator，絕不決定 ownership。
3. **Cohesive domain ownership**：baseline 有 421 unique handlers；12 個 domain tasks 合計擁有 441 decorators。存在 router 時直接擴充；只有沒有 canonical owner 時才建立具名 router。沒有 generic `route_families` 或 catch-all module。
4. **功能與 route extraction 合併**：BFF core、Agora/research、Management/Postmortem、command adapters、RuntimeBinding、deployment reliability 同時擁有其 domain 的功能修復與 inline route 搬移，避免兩個平行 semantic owners。
5. **Command callers 先切、legacy 後刪**：BFF adapters/tests、BFF main、Compose/env、runtime mount、deployment pipeline、CLI、ops scripts、top-level shims、runtime smoke/hardening tests 與 stage-0 matrix 都有明確 owner；historical docs/evidence 只能留在 catalog 的 non-executable allowlist。
6. **Source 不重工**：沒有 Source source-code task。OP-G12 只做 hosted effect proof。
7. **單倉庫任務**：Pantheon 與 execute-plans tasks 分開；只以 dependencies compose。
8. **Materialization fail closed**：Batch A 先修復 signed bridge 遺失 `target_repo` 的實測缺口；authoritative two-repo materialize/readback 合併且 done 後才能送 B/C。
9. **Prior terminal delivery 先對帳**：2026-08-28 catalog 的 28 個 ACG terminal tasks與 4 個 relevant PFG hosted tasks全部保留、不重開、不標 superseded；每列記錄 current-code evidence、可重用 delivery、claim 的 still_true/partial/contradicted、exact residual與至多一個 follow-up。
10. **既有 active scope 不重開**：`AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` 已擁有 `main.py`，Main Assembly 等待 terminal；`AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` 是 OP-G14 唯一 lane，不再建立重複 hosted FE task。
11. **Counts 是結果，不是 validator input**：22 children、12 route owners、batches 1/12/9 是目前 bounded-context review 的衍生結果。Machine gate只驗唯一 coverage、max-16、dependency closure、bootstrap-first 與 live capacity，不用預設 count 證明自己。
12. **Structural green 不等於 approval**：Codex2 必須逐 task 審 responsibility、owned/excluded artifacts、acceptance/readback、serialization edge、terminal predecessor residual，以及每個 new-router no-canonical-owner assertion。

## 2. 文件權威

| 文件 | 權威內容 |
|---|---|
| [`CURRENT_GAP_DISPOSITION_2026-08-30.md`](CURRENT_GAP_DISPOSITION_2026-08-30.md) | 20 roots 的 current disposition、證據、退役與 no-rework boundary |
| [`SA_GAP_REMEDIATION_2026-08-30.md`](SA_GAP_REMEDIATION_2026-08-30.md) | domain ownership、composition、command retirement、Source 與 delivery boundary |
| [`SD_GAP_REMEDIATION_2026-08-30.md`](SD_GAP_REMEDIATION_2026-08-30.md) | implementation units、transition、tests、rollback/readback |
| [`EXECUTION_DAG_2026-08-30.md`](EXECUTION_DAG_2026-08-30.md) | Waves、dependencies、hot files、materialization batches、capacity-one scheduling |
| [`EXECUTION_TASK_CATALOG_2026-08-30.json`](EXECUTION_TASK_CATALOG_2026-08-30.json) | 唯一 machine-readable task、route assignment、caller inventory 與 materialization truth |

其他五檔不得覆寫 catalog 的 task ID、dependency、artifact、route owner 或 target router。

## 3. GAP owner 摘要

| GAP | 狀態 | 唯一 primary owner 或 terminal evidence |
|---|---|---|
| OP-G01, OP-G02, OP-G09 | active | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G03 | **closed** | accepted pair `6899d0da...`；無 implementation task |
| OP-G04, OP-G16 | active | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| OP-G05, OP-G13 | active | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G06 | active | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` |
| OP-G07 | active | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| OP-G08 | active | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| OP-G10 | active | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| OP-G11, OP-G12 | verify | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` |
| OP-G14 | blocked | existing `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`；不 materialize duplicate |
| OP-G15 | active | `OPGAP-FE-AGORA-WORKSHOP-20260830` |
| OP-G17 | active | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| OP-G18 | active | `OPGAP-BE-MGMT-POSTMORTEM-20260830` |
| OP-G19, OP-G20 | verify | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |

OP-G21 的 multi-replica/import-cycle/operation-ID observations 併入 OP-G08；OP-G24 併入 OP-G10；OP-G25 的產品發布行為併入 OP-G04/OP-G16。其他非功能觀察不產生本輪 task。

## 4. Route domain inventory

| Owner task | Decorators | Target router boundary |
|---|---:|---|
| `OPGAP-BE-BFF-CORE-20260830` | 30 | assistant、auth、core、settings |
| `OPGAP-ROUTE-PERSONA-TRAINING-20260830` | 63 | personas、training |
| `OPGAP-BE-AGORA-RESEARCH-20260830` | 85 | existing Agora subrouters + research |
| `OPGAP-ROUTE-GOVERNANCE-EVOLUTION-20260830` | 48 | governance + existing evolution |
| `OPGAP-ROUTE-CAPITAL-STRATEGY-20260830` | 56 | capital、strategies、existing ranking read model |
| `OPGAP-BE-MGMT-POSTMORTEM-20260830` | 19 | management read models、postmortems |
| `OPGAP-BE-COMMAND-ADAPTERS-20260830` | 11 | existing command adapter router |
| `OPGAP-BE-RUNTIME-BINDING-20260830` | 17 | runtime |
| `OPGAP-DEPLOY-RELIABILITY-20260830` | 12 | deployment |
| `OPGAP-ROUTE-INCIDENT-EVENTS-20260830` | 41 | incidents + existing events |
| `OPGAP-ROUTE-TOOLS-INTEGRATIONS-20260830` | 35 | integrations |
| `OPGAP-ROUTE-CONTROL-LOOPS-20260830` | 24 | control loops |
| **Total** | **441** | exact assignment rows in catalog |

421 handler dispositions中，420 筆是 `move_as_unit`：同 handler 的所有 aliases 必須到同一 task/router；例如 `bff_agora_research_tasks` 的兩條 routes 都移到 Agora research router。唯一 `decompose_generic` 是 `sem_final_generic_read_alias`：Governance/Research 各建立 typed handler，不複製 generic dispatcher，最後只由 Main Assembly 刪除 old handler。

任何 baseline drift、未列 decorator、同一 method+normalized-path 多 owner、或 target router 改名，都必須重新審查 catalog；不能在 implementation task 內自行調整。

Target registry另把 30 個 routers逐一分成 16 個 `existing_at_baseline`（附 blob proof）與 14 個 `new_no_canonical_router`（exact path absent + semantic assertion）。New router assertion 必須由 reviewer判讀，不能只靠 file absence。

## 5. Materialization contract

1. Plan exact-head review、merge、done。
2. Batch A materialize `OPGAP-DEVTOOL-TARGET-REPO-READBACK-20260830`。
3. Bootstrap 在 isolated authoritative event log 中，透過 governed materialize + readback 建立 Pantheon 與 execute-plans 各一列；exact `target_repo`、artifacts、dependencies、execution resources 與 `task_spec_hash` 必須不變。
4. Bootstrap PR merge 且 task done 後，Batch B materialize目前衍生的 Pantheon cohesive domain tasks。
5. Batch C materialize 3 FE domain tasks、2 assembly tasks、caller cutover、retirement、promotion 與 hosted backend acceptance，共 9 rows；OP-G14 留在既有 AGC-14 row，blocker 改變後才 resume。

任何 batch 超過 16、repo readback 遺失、artifact/repo 不相容、dependency closure 不成立，都必須在 canonical mutation 前 fail closed。

## 6. Machine checks

```bash
CAT=docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json

jq -e '.route_migration_inventory.decorator_count == 441
  and (.route_migration_inventory.assignments | length) == 441
  and ([.route_migration_inventory.domain_owners[].decorator_count] | add) == 441
  and (.route_migration_inventory.handler_migration_dispositions | length) == 421
  and ([.route_migration_inventory.handler_migration_dispositions[].handler] | unique | length) == 421' "$CAT"

jq -e '[.gap_dispositions[].gap_id] | sort ==
  ["OP-G01","OP-G02","OP-G03","OP-G04","OP-G05","OP-G06","OP-G07","OP-G08","OP-G09","OP-G10","OP-G11","OP-G12","OP-G13","OP-G14","OP-G15","OP-G16","OP-G17","OP-G18","OP-G19","OP-G20"]' "$CAT"

jq -e '.gap_dispositions[] | select(.gap_id == "OP-G03")
  | .state == "closed" and .owner_task == null' "$CAT"

jq -e '([.prior_delivery_dispositions[] | select(.task_id | startswith("ACG-"))] | length) == 28
  and ([.prior_delivery_dispositions[] | select(.task_id | startswith("PFG-"))] | length) == 4
  and all(.prior_delivery_dispositions[];
    .terminal_status == "done"
    and (.acceptance_claim == "still_true" or .acceptance_claim == "partial" or .acceptance_claim == "contradicted")
    and .terminal_record_disposition == "preserved_not_reopened_or_superseded")' "$CAT"

jq -e '(.materialization_contract.batches[0].tasks ==
    [.materialization_contract.bootstrap_task_id])
  and ([.materialization_contract.batches[].tasks[]] | length) == (.tasks | length)
  and ([.materialization_contract.batches[].tasks[]] | unique | length) == (.tasks | length)
  and all(.materialization_contract.batches[].tasks | length <=
    .rules.materialization_batches_max_tasks)' "$CAT"
```

Repository validation另需檢查 441 AST parity、421 handler parity、`main_symbol_inventory`、28 ACG archive completeness、4 PFG hosted dispositions、current FE residual/absent-path truth、method+normalized-path 唯一性、artifact collision、GAP↔task parity、dependency-track parity、acyclic graph、batch coverage、single-repo artifacts、command caller ownership、non-executable allowlist、no Source code task 與 `git diff --check`。

## 7. Runnable capacity 與 current overlap

Governed command runtime `954caefa...` 的 non-Claude capacities 是 Antigravity=4、Antigravity2=4、Codex=2、Codex2=2。Bootstrap terminal 後，current Batch-B 12 independent lanes分配正好是 4/4/2/2；每個 owner不超過 `max_parallel` 並使用全部可用 non-Claude capacity。若 config SHA改變，必須重新推導，不能保留舊 count。

Authoritative nonterminal snapshot已逐一列入 catalog：兩個 Source recovery tasks與本 plan無 Source artifact；既有 AGC-14保留固定 evidence artifacts並成為 OP-G14唯一 owner；唯一 source artifact overlap是 Persona durable-readback task與 Main Assembly的 `main.py`。該 overlap有 explicit dependency，且 Batch C 要求 predecessor terminal 才可 materialize Main Assembly。AGC-14 的既有 scope/dependencies不重寫，只在其 paper baseline bootstrap HTTP 500 blocker有新證據後 resume。

## 8. Review 與 closeout

- Owner：`Codex`；Reviewer：`Codex2`。
- `REVIEW_FILE` 固定為 committed catalog。
- Handoff 綁 PR current 40-hex head；不得重用 rejected/stale binding。
- Reviewer 只審 exact head。Implementation tasks 在 plan done 前不得 materialize；Batch B/C 在 bootstrap done 前不得 materialize。
- Structural validator只提供 consistency evidence，不構成 bounded-context semantic approval。
