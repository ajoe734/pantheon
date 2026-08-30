# Pantheon 全產品運作 GAP Remediation — System Design — 2026-08-30

## 1. Design contract

Machine truth 是 [`EXECUTION_TASK_CATALOG_2026-08-30.json`](EXECUTION_TASK_CATALOG_2026-08-30.json)。本文件描述 transition/test/rollback；task ID、完整 artifact list、dependencies、route rows 與 target routers 以 catalog 為準。

目前 task/owner/batch counts 是 semantic decomposition的輸出，不是 validator input。Structural pass不代表 design approval；Codex2逐 task審 responsibility、owned/excluded artifacts、acceptance/readback與serialization edge。

共同規則：

- 每個 task 只寫一個 repository。
- Route work 以 catalog 的 explicit `(method, normalized_path)` rows 為準；source line 不定義 ownership。
- 有 existing domain router 就 extend/reconcile；沒有才建立具名 domain router。
- Domain task 不改/import `main.py`；Main Assembly 是 sole `main.py` owner。
- 同一 method+normalized-path 最終只能有一個 registration/semantic owner。
- Timeout、skip、missing result 不得計為 pass。
- Rollback 不得同時啟用新舊 write owner。
- No Source implementation artifact。
- Prior terminal task保持 archived fact；每個 follow-up引用 predecessor、排除已交付 scope，只擁有 catalog 的 current-code residual。

## 2. SD-01 — target_repo materialization bootstrap

Task：`OPGAP-DEVTOOL-TARGET-REPO-READBACK-20260830`。

Transition：

```text
BridgeTask target_repo
  -> signed task spec / task_spec_hash
  -> canonical task mutation
  -> immutable materialize-readback comparison
```

Tests 必須在 isolated authoritative event log 中 materialize Pantheon 與 execute-plans samples，讀回 exact repo、artifacts、dependencies、resources、hash；mixed/unknown repo 或 artifact conflict 在 mutation 前失敗。它不加 product route。Rollback 是整個 tooling unit revert；Batch B/C 仍 closed。

## 3. SD-02..SD-13 — cohesive Pantheon domains

所有 12 tasks 都依賴 plan + bootstrap。每個 task 的 route acceptance 由 catalog assignments filtered by `owner_task` 決定，不能只驗總數。

| SD | Task | Decorators | Owned transition | Required proof |
|---:|---|---:|---|---|
| 02 | `OPGAP-BE-BFF-CORE-20260830` | 30 | local auth 與 provider readiness decouple；async ASGI tests；assistant/auth/core/settings routes 移入 target routers | provider slow/offline 不延遲 auth；hard deadline；30-row parity |
| 03 | `OPGAP-ROUTE-PERSONA-TRAINING-20260830` | 63 | personas/training named routers；保留既有 Persona durable-readback predecessor contract | 63-row parity；session/training/persona schemas；no second Persona owner |
| 04 | `OPGAP-BE-AGORA-RESEARCH-20260830` | 85 | admitted provenance、durable suggestion、one Workshop store；inline routes reconcile into existing Agora subrouters/research router | provenance negatives；trigger-ID reload；private imports zero；85-row parity |
| 05 | `OPGAP-ROUTE-GOVERNANCE-EVOLUTION-20260830` | 48 | governance named router + existing evolution router；consult/approval/review/lineage/telemetry single ownership | method/path/schema parity；existing evolution routes preserved；48-row parity |
| 06 | `OPGAP-ROUTE-CAPITAL-STRATEGY-20260830` | 56 | capital/strategies routers + existing ranking read model | typed domain boundaries；ranking readback parity；56-row parity |
| 07 | `OPGAP-BE-MGMT-POSTMORTEM-20260830` | 19 | canonical Postmortem list/detail/restart；management read model + named postmortem router | canonical `postmortem_id`；no `pm_<incident>`/second store；19-row parity |
| 08 | `OPGAP-BE-COMMAND-ADAPTERS-20260830` | 11 | BFF registry/executor calls typed domain owners；all BFF caller tests migrate；existing command router owns command routes | legacy env/path zero in owned code/tests；fail-closed receipts；11-row parity |
| 09 | `OPGAP-BE-RUNTIME-BINDING-20260830` | 17 | Registry immutable projection -> Deployment ref -> Runtime admission -> paper receipt；runtime router extraction | checksum/authority rejection；natural signal receipt；17-row parity |
| 10 | `OPGAP-DEPLOY-RELIABILITY-20260830` | 12 | bounded lease heartbeat、sealed local rollback、required-result aggregation；deployment router extraction | remote outage drill；failed/skipped/missing non-zero；12-row parity |
| 11 | `OPGAP-ROUTE-INCIDENT-EVENTS-20260830` | 41 | named incidents router + existing events router；incident/alert/risk/audit/SSE ownership | stream contract、disconnect、same-ID incident timeline；41-row parity |
| 12 | `OPGAP-ROUTE-TOOLS-INTEGRATIONS-20260830` | 35 | named integrations router for tools/MCP/skills/channels/OpenClaw ops | alias reconciliation；typed ports；35-row parity |
| 13 | `OPGAP-ROUTE-CONTROL-LOOPS-20260830` | 24 | named control-loops router for interventions/sentinel/loops/OODA | read/command parity；no generic fallback；24-row parity |

421 handler dispositions各自 exactly once。420 筆 `move_as_unit` 必須維持 one owner/target；`bff_agora_research_tasks` aliases一起到 Agora research router。唯一 `decompose_generic` 的三條 routes各建立 typed replacement，domain tasks 不碰 main；Main Assembly最後刪除 `sem_final_generic_read_alias`。

### 3.1 Router transition algorithm

每個 domain task：

1. 從 catalog 取自己的 decorator rows 與 handler dispositions，驗證 source baseline SHA、441/421 counts。
2. 以 target router 分組；若 target 已存在，先 inventory equivalent registrations。
3. 把 handler logic 改為 injected typed ports/store，保留 method、raw path behavior、auth、status、response schema、operation identity。
4. `move_as_unit` aliases 共用一個 implementation/target；`decompose_generic` 只建立明列 typed handlers，不複製 generic fallback。
5. 在獨立 router tests 驗每個 assigned row；不 mount 到 main。
6. 提交後輸出 row-level parity receipt。

若 source drift、unassigned decorator、跨 task ownership、或 target 不再合理，停止並重新審查 catalog，不得用新的 line grouping 臨時補洞。

### 3.2 Domain rollback

Domain PR rollback 只移除未 mount 的 router changes，不影響 serving `main.py`。若 task 同時修復 domain feature，feature + router changes 作為一個 unit revert；不得留下 router 指向舊/新兩套 store。

## 4. SD-14..SD-16 — execute-plans domain tasks

| SD | Task | Transition | Proof / rollback |
|---:|---|---|---|
| 14 | `OPGAP-FE-BUNDLE-CLEANUP-20260830` | 只清除 `frontend_residual_inventory` 列出的 current production seed/mock/overlay reachability；保留 ACG 已完成的 old-path deletions、depgraph與 live SSE consolidation | 已刪 paths仍 absent；既有 productionImportGraph + new Rollup forbidden-symbol gate zero；strict-live fail visible；整個 residual unit revert |
| 15 | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` | enabled mutations bind typed owner；unsupported controls disabled；Postmortem UI 用 canonical ID | component/client tests + same-ID reload；無 overlay fallback |
| 16 | `OPGAP-FE-AGORA-WORKSHOP-20260830` | backend capability 驅動 truth badge/admission；dynamic pool；durable suggestion widget | real/simulated/unavailable matrix；trigger-ID reload；rollback 不可把 simulation 標 real |

三者都在 execute-plans，不寫 Pantheon artifacts。SD-15 等待 Postmortem backend；SD-16 等待 Agora backend。

## 5. SD-17 — BFF main assembly

Task：`OPGAP-BFF-MAIN-ASSEMBLY-20260830`；sole `services/control-plane/bff/main.py` owner。

Preconditions：

- 12 cohesive domain tasks reviewed/merged；
- `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` terminal；
- AST 重現 68,171 lines、1,727 top-level function/class symbols、421 handlers、441 decorators、441 assignments、421 dispositions與 `main_symbol_inventory`；
- 明確承認 `ACG-BFF-MAIN-CUTOVER-20260828` composition-only與 `ACG-RS-FINAL-DELETE-20260828` delete claims被 current code contradicted，不以 terminal label略過 residual。

Transition：

```text
12 tested domain-router outputs
  -> construct typed dependencies
  -> include_router
  -> compare every method + normalized path + contract + imported symbol/caller
  -> remove 441 inline decorators and obsolete helpers
  -> reconcile 66 ReadSurfaceStore references, then delete read_store.py
  -> normalized/static/operation-ID gates
  -> multi-replica load
```

Final main 只保留 app/middleware/lifespan/dependency composition。它同時移除 legacy command status/readout。只 mount routers不是 acceptance；任何 count/key/symbol drift、missing route、broken inventoried importer、duplicate operation ID、static shadowing、domain import of main、remaining ReadSurfaceStore caller或 replica load failure都阻擋 merge。

Rollback 回到 assembly 前 main 並整體 unmount 新 routers；不接受 half-mounted state。Domain router PR 不必 rollback，因為 assembly 前不 serving。

## 6. SD-18 — execute-plans integration assembly

Task：`OPGAP-FE-INTEGRATION-ASSEMBLY-20260830`。

Sole hot files：`App.tsx`、`ManagementLayout.tsx`、`bff-v1/index.ts`。它 mount completed Management/Agora pages、移除 dead navigation、export typed clients。Typecheck、unit、production build皆通過；不寫 Pantheon 或 hosted evidence。

Rollback 使用上一個 app shell，已 merge domain pages 可暫時 unreachable，但不得恢復 mixed transport。

## 7. SD-19 — external command caller cutover

Task：`OPGAP-BE-COMMAND-CALLER-CUTOVER-20260830`。

Artifact set 必須精確等於 catalog 的 10-entry `required_command_caller_artifacts`：

- `.env.example`、`env/prod-control.env.example`；
- `docker-compose.yml`、`docker-compose.control.yml`、`docker-compose.staging-full.yml`；
- `services/runtime-manager/main.py`、`services/deployment/promote_pipeline.py`；
- `scripts/validate_split_topology.sh`、`scripts/smoke_operator_fallback_drills.py`、`tools/pantheon_admin/cli.py`。

所有 URL/env/mount caller 移到 typed owner；runtime manager unregisters `internal_api_routes`。CLI/drills 保留既有 request、idempotency 與 fail-closed receipt behavior。三份 Compose、兩份 env example 驗證，legacy env/path/module refs zero。

Rollback 可在 legacy implementation 尚存時整批 revert 10 files；retirement 開始後不可單獨 rollback。

## 8. SD-20 — central command-plane retirement

Task：`OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`。

Preconditions：BFF adapters、Main Assembly、external cutover done。Catalog `command_retirement_inventory.owner_sets` 必須涵蓋每個 executable/import/config/workflow/test reference。

Delete/rewrite unit 包含：

- kebab implementation + dedicated test；
- underscore package shims + missing top-level `services/control_plane/internal_api.py` 與 `internal_api_min.py`；
- runtime `internal_api_routes.py`、internal route test、`smoke_test.py`、`test_runtime_hardening.py`；
- `tests/run_internal_api_smoke.py`；
- `.github/pantheon-stage0-matrix.json` 的 compile/watch/run entries；
- active contracts/runbooks 與 test migration inventory。

Repository gate 對 forbidden env、URL、module/import、shim symbols fail closed。只有 catalog 明列的 non-executable historical allowlist 可保留文字，且 allowlist path 不得是 executable、config、workflow 或 test。

Typed domain regression suites通過，`command_executor.py` 保留。Rollback 只允許在後續 promotion 前 revert 整個 retirement；不得新建 forwarding shim。

## 9. SD-21 — changed-head hosted promotion

Task：`OPGAP-HOSTED-DEV-PROMOTION-20260830`；`gaps=[OP-G19,OP-G20]`，不含 OP-G03。

它重用三個 PFG terminal deliveries的 fail-closed verifier、evidence schema與 bounded current-dev admission；歷史 exact pairs維持歷史，不重試 `PFG-HOSTED-RUNTIME-CLOSEOUT-20260828` 已證明不可 admission 的 hard-coded pair。

在 `pantheon-dev` capacity-one lease 中：

1. bind exact reviewed Pantheon + execute-plans heads；
2. stage candidate，不 switch；
3. verify all service health/checkpoints；
4. verify latest exact `connectorId + ingestRunId + sourceId` projection；
5. verify producer ready + one natural same-ID signal/order/fill/position/heartbeat；
6. seal rollback baseline、atomic switch、post-switch probes；
7. write immutable Pantheon evidence。

Mismatch 保留舊 pair。Promotion 不編輯 Compose hot files。

## 10. SD-22 — hosted backend/Source acceptance

Task：`OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830`。

- Loop 1..12 各有 natural stimulus、canonical owner receipt、terminal state、same-ID Management readback。
- Source 使用 existing code：create test source -> validate -> max-one-tick/max-100-record one-shot -> official snapshot -> terminal scheduler -> automatic `reconcile_only`。
- Evidence 綁 candidate pair/checkpoints/egress bounds。
- 無 Source production code或第二 refresh endpoint artifact。

Failure keeps task nonterminal；cleanup test data並確認 controller reconcile-only。

## 11. SD-23 — existing hosted frontend acceptance reuse

Existing task：`AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`，target repo execute-plans；不是本 catalog materialized child。

沿用該 task固定的 Playwright/auth/helper/evidence artifacts與 `PFG-FE-DEPLOY-PROBE-RETRY-20260828` bounded retry。其 current recorded blocker是 paper baseline bootstrap HTTP 500，故 public FE admission、real login/browser journey與 watchdog proof尚未執行。只有 blocker有新證據後才 resume原 scope；不得建立 duplicate task或改寫 canonical dependencies。

Failure 不產生 success evidence；cleanup session/data；previous accepted pair保持可回復。

## 12. Verification matrix

| Boundary | Required proof |
|---|---|
| Materialization | signed literal repo -> canonical row -> immutable two-repo readback |
| Routes | 441 assignment rows + 421 handler dispositions；move-as-unit one target；decompose-generic typed replacements完整 |
| Router architecture | existing router reuse；named router only when absent；no line-band/generic/catch-all owner |
| Main | current 68,171-line/1,727-symbol inventory reproduced；route+symbol/caller parity；zero inline decorators/reverse imports/ReadSurfaceStore refs/collisions/duplicate IDs；multi-replica |
| Artifacts | exact/prefix overlap zero；single repository per task |
| Dependencies | tracks equal depends；acyclic；bootstrap first；每批 ≤16、dependency-closed、atomic；all children exactly once |
| GAP parity | 20 rows；OP-G03 closed/null；18 catalog active/verify IDs exactly once；OP-G14 blocked owner is existing AGC-14 |
| Command retirement | every executable/import/config/workflow/test ref owned；stage-0/shims/smoke/hardening covered；historical allowlist non-executable |
| Source | no Source code artifact/task；bounded hosted effect only |
| Hosted | capacity-one serialized promotion -> backend -> existing AGC-14 resume；無 duplicate FE child |
| Prior delivery | 28 ACG + 4 PFG terminal rows完整；current evidence/reusable artifacts/claim/residual/zero-or-one follow-up；never superseded |
| Dispatch | owner != reviewer；live agents only；initial runnable per owner ≤ governed max；足夠 lanes時使用全部 non-Claude capacity |
| Review | structural green不是 approval；每個 bounded context、excluded artifact與 new-router assertion均需 exact-head semantic review |
