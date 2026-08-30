# Pantheon GAP Remediation — System Design (SD)

## 1. 設計範圍與交付單元

Catalog 含 1 個 plan-freeze 與 30 個 execution/support tasks。數量由 repo、bounded context、hot-file ownership、shared port/handler atomicity 與不可逆退役邊界導出；OP-G14 沿用既有 canonical execute-plans task，不計入新 materialization。完整 artifact、acceptance、dependency、167 個 port caller inventory 與 441 條 route assignment 以 [EXECUTION_TASK_CATALOG_2026-08-30.json](EXECUTION_TASK_CATALOG_2026-08-30.json) 為機器真相。

| Unit | Task | 設計責任 |
|---|---|---|
| SD-00 | `OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830` | immutable cross-repo task routing/readback |
| SD-P | `OPGAP-BE-PORT-NAMESPACE-CONSOLIDATION-20260830` | 唯一 shared-port public/implementation namespace；caller migration + duplicate tree deletion |
| SD-01 | `OPGAP-BE-SEMANTIC-ALIAS-SERVICE-20260830` | 唯一跨域 read-handler implementation |
| SD-02..18 | 17 個 BFF bounded-context preparation tasks | 441 routes 的逐條搬移；不得改 main |
| SD-19 | `OPGAP-BE-RUNTIME-BINDING-20260830` | Registry-authoritative immutable binding |
| SD-20 | `OPGAP-DEPLOY-RELIABILITY-20260830` | lease/rollback/acceptance fail-closed |
| SD-21 | `OPGAP-FE-BUNDLE-CLEANUP-20260830` | production mock/overlay/fallback delete |
| SD-22 | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` | typed writes + canonical Postmortem UI |
| SD-23 | `OPGAP-FE-AGORA-WORKSHOP-20260830` | truthful Agora UI |
| SD-24 | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` | main-only composition/cutover |
| SD-25 | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` | irreversible central-plane delete |
| SD-26 | `OPGAP-FE-INTEGRATION-ASSEMBLY-20260830` | FE shell/client hot files |
| SD-27 | `OPGAP-HOSTED-DEV-PROMOTION-20260830` | exact candidate promotion |
| SD-28 | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` | Pantheon 12-loop/Source hosted proof |
| Existing OP-G14 lane | `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` | 既有 blocked execute-plans authenticated desktop proof；不重複 materialize |

OP-G03 已在 planning baseline 關閉，不對應 execution unit。F22/F23/F25 不在本 functional-first design 中。

## 2. SD-00 — target_repo immutable bridge

### Current defect

目前 `BridgeTask` 沒有 canonical `target_repo` field，dispatcher `task_spec`/hash、admission 與 `ai_status` materialize readback 也未端到端保存它。因此 execute-plans task 可能在 JSON/preflight 看似正確，canonical readback 卻只剩 pantheon default。

### Design

- `BridgeTask` 接受 canonical `target_repo`，只支援明列的 legacy aliases。
- `target_repo` 進入 signed packet、task-spec hash、admission comparison、assignment payload、archive receipt 與 canonical TaskStore。
- materialize readback 回傳 `targetRepo`、`artifactRepoIds`、`taskSpecHash`。
- task spec 與 canonical task 的 repo 不一致時 fail-closed；不得悄悄 fallback。
- legacy packet 完全沒有欄位時才使用明文化的 `pantheon` default。
- tests 覆蓋 execute_plans、pantheon、tamper、alias、legacy omission、replay/readback。

### Bootstrap

本 task 自身是 pantheon-default，可先用現行 bridge materialize。它合併前其餘 29 execution records 不得宣稱已 materialized；合併後全部重新經 canonical materialize/readback。既有 OP-G14 與三個其他 reconciled nonterminal rows 不重新 materialize。

## 3. SD-P — canonical shared-port namespace

### Current defect

exact caller scan 發現 167 個 unique import files：150 import `services.control_plane.bff.ports`/等價 package path，22 import `domain_ports`，其中 5 個同時 import。六個 `domain_ports` modules 是實際 domain contract implementation；六個同名 `ports` modules 直接 forwarding/fallback，`read_surface_ports.py` 另直接做 composition，讓 package boundary 有兩個合法入口。

### Design

- canonical namespace 是 `services/control-plane/bff/ports`，同時承擔 stable public API 與 implementation；不把它誤稱為無邏輯 facade，因為現有 package 已含 factories/composition。
- 將 `lifecycle_telemetry_governance`、`ooda_management`、`operations_consultation`、`persona_capital_runtime`、`persona_training`、`research_knowledge_source` 六組 implementation 合併到同名 `ports` modules，保持 symbol、protocol、factory 與 error semantics。
- 遷移全部 22 個 direct `domain_ports` callers；5 個 dual-import callers 收斂成單一 `ports` import；150 個既有 `ports` callers 維持 canonical path。
- `ports/read_surface_ports.py` 只允許 composition/delegation/test factory，不建立 persistence、mutation authority 或第二 implementation owner。
- zero direct caller 後，同一 task 刪除六個 `domain_ports` files；boundary test 拒絕 `domain_ports` 與任何第三 port/compat namespace。
- transition 由八個 canonical ACG-RS terminal tasks 的 current residual 導出：foundation、OPS/consult、OODA/Management、Research/Source、Persona/Training、Persona/Capital、Lifecycle/Governance 與 final delete；歷史 maps/`REVIEW_EVIDENCE.md` 僅作 non-executable allowlist。
- 一個 direct caller test 與活動 task `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` 重疊，因此 SD-P 等其 merge 後 rebase，只遷移 namespace，不修改 Persona 行為。

所有 route preparation 與 SD-01 依賴 SD-P。這個順序確保新 routers 不會把雙 namespace 繼續帶入新的 bounded-context tree。

## 4. Route assignment contract

Baseline extraction 使用 Python AST，僅計 FastAPI HTTP/websocket decorators。Catalog 每列保存：

```text
method
normalized_path
source_handler
source_line
target_owner_module
preparation_task
handler_implementation_owner_task
migration_mode
```

Gates：

1. entries = 441；
2. unique `method + normalized_path` = 441；
3. entries 與 baseline main AST set 完全相等；
4. 421 source handlers 各只有一個 implementation owner；
5. route preparation/implementation tasks 均不擁有 `main.py`；
6. 每個 preparation task 在 catalog 存在且 target module 是具名 bounded context；
7. assembly 後 OpenAPI operation ID、normalized route、shadowing collision 都為 0；
8. preparation task 只 import canonical `ports` namespace，且 `domain_ports`/第三 namespace 為 0。

## 5. SD-01 — cross-domain handler atomicity

`sem_final_generic_read_alias` 同一 handler 掛三個 decorators：

- `GET /bff/approvals/{param}` → Governance route owner；
- `GET /bff/artifacts/{param}` → Research route owner；
- `GET /bff/research-analyses` → Research route owner。

設計不是共用 router，也不是複製三份 handler。SD-01 建立 `semantic_alias_read_service.py`，僅包含這三個 contract 需要的 typed read implementation，不含 `APIRouter`、decorator、store 或 compatibility routing。Governance/Research tasks 依賴它並各自建立薄 wrapper。Main assembly 最後刪除原 inline handler。

## 6. SD-02..18 — bounded-context router designs

| Unit / task | Routes | Target owner | Boundary/atomicity |
|---|---:|---|---|
| SD-02 Core | 14 | `core/router.py` | auth/session/health；local JWT/RBAC；provider readiness 背景化 |
| SD-03 Agora | 40 | existing `agora/router.py` tree | 所有 Agora aliases/stream 同 owner；receipt-backed real；單一 Workshop store |
| SD-04 Management | 63 | existing `management_read_models/router.py` | projection-only；Persona league dual-decorator 留 Persona |
| SD-05 BFF v5 | 24 | `v5/router.py` | intervention multi-decorators atomic；不建 v5 domain/store |
| SD-06 Research | 37 | existing `research/router.py` | research/knowledge/artifact/lineage；generic read 用 SD-01 thin wrapper |
| SD-07 Evolution | 10 | existing `evolution/router.py`/jobs | experiment/evolution/OODA；不建 legacy API router |
| SD-08 Persona | 76 | `persona/router.py` | persona/trainer/consultation/strategy/league；不與 Management 重複 |
| SD-09 Capital | 23 | `capital/router.py` | pools/ranking/rebalance；沿用 capital authority |
| SD-10 Governance | 28 | `governance/router.py` | approvals/reviews/audit/confirm；generic read 用 SD-01 thin wrapper |
| SD-11 Runtime | 27 | existing `runtime_routes.py` | deployment/binding/runtime/telemetry；不吸收 Incident/Operator |
| SD-12 Incident | 27 | `incident/router.py` | incidents/alerts/kill-switch/postmortems；五個 command aliases 同 handler owner |
| SD-13 Tools | 25 | `tools/router.py` | tools/MCP/skills；沿用 registries |
| SD-14 Events | 5 | existing `events/router.py` | 只擁 generic SSE/channels；domain streams 回 domain |
| SD-15 Operator | 33 | `operator/router.py` | operations presentation projections；command 不在此 task |
| SD-16 Settings | 4 | `settings/router.py` | CRUD/import/export 沿用唯一 settings store |
| SD-17 Assistant | 1 | existing `assistant/routes.py` | usage summary 回既有 assistant owner |
| SD-18 Command | 4 | existing `command_adapters/router.py` | commands/actions/MCP import；與 caller cutover 同 owner |

### SD-02 Core details

- `create_core_router(CoreDependencies)` 不讀 module-global app。
- auth 只做 local signature、tenant、RBAC；OpenClaw/provider probe 不在 request critical path。
- HTTP tests 使用 `httpx.AsyncClient(ASGITransport)` 與 bounded deadline；timeout/skip 不算 pass。

### SD-03 Agora details

- `provenance=real` 只能由 admitted adapter receipt ID 推導；fallback 是 simulated/unavailable。
- PerformanceSuggestion producer 連到既有 durable event/outbox，identity 含 source event ID，支持 idempotent same-ID readback；找不到自然 production event 就刪除產品宣稱，不新增 polling loop。
- existing Agora aggregate router 包含 subrouters；禁止 private cross-router imports 與第二 store。
- `bff_agora_research_tasks` 的 `/bff/agora/research-tasks` 與 `/bff/research/tasks` 兩 decorators 一起移動。

### SD-12 Incident/Postmortem details

- Incident、alerts、kill-switch reads 與 postmortems 屬同一 incident lifecycle。
- Postmortem 透過 canonical `services/control-plane/bff/ports` typed domain port 呼叫既有 `services/postmortems`；BFF 不新增 store，也不 import `domain_ports`。
- DTO 使用 canonical `postmortem_id`；`pm_<incident>` alias、相關 callers/tests 同 owner 移除。
- `sem_final_generic_id_command_alias` 的五個 decorators 整組移動，不拆 implementation。

## 7. SD-18 — central-command caller cutover

此 task 同時是 4 條 command routes 的 preparation owner與 pre-retirement caller owner。

### Owned caller classes

- `.env.example`、prod env example；
- dev/control/staging 三份 Compose；
- split-topology validator、operator fallback drill 與其 test；
- BFF base + capital/deployment/governance/incident/persona/runtime adapters、registry/router、executor、health monitor；
- 相關 BFF fixtures/contracts；
- deployment promotion caller；
- `tools/pantheon_admin/cli.py`。

共 29 direct references + 1 indirect test caller。所有 caller 改用 typed per-domain adapters/URLs，保留 command ID、idempotency key、receipt mapping。移除 `PANTHEON_INTERNAL_API_URL` 與 central fallback；`command_executor.py` 保留。找不到 canonical adapter 的 action 必須 unavailable/fail-closed，不能回退中央 facade。

## 8. SD-19 — immutable RuntimeBinding

- Registry 產 versioned immutable projection：artifact digest、object-store locator、loader projection、market policy、authority metadata。
- Deployment 只接受 projection reference/digest，拒絕 caller-supplied executable metadata。
- Runtime Manager reload projection、驗 authority/digest 後建立 RuntimeBinding；不一致是 terminal failure。
- admitted Taiwan official snapshot 可自然驅動 Paper producer；直接呼叫 helper 不算 OP-G20 proof。

## 9. SD-20 — deployment reliability

- lease heartbeat 使用 bounded exponential retry；transient remote failure最多 60 秒 local grace，authority expiry 仍 fail-closed。
- switch 前封存 served manifest/image/symlink 到 local sealed rollback baseline；rollback 不依 GitHub availability。
- 每個 required auth/write/readback/identity check 產 structured terminal result；failed/skipped/missing/malformed 均 non-zero。
- 此 unit 不宣稱修復 F25 organization branch enforcement。

## 10. SD-21..23 — frontend domain work

### SD-21 Bundle cleanup

- `src/lib/bff-v1` 是唯一 production transport；v5 只做 DTO/view transform。
- Rollup/Vite dependency scanner 從 production entries 出發，禁止 mock/seed/overlay/fallback reachability。
- 刪除 dead implementation/export，而不是只移除 import 或換名稱。

### SD-22 Management typed writes/Postmortem

- enabled form 必須有 concrete typed command + durable receipt/readback。
- 無 owner 的 generic entity control disabled/removed；不新增 generic CRUD backend。
- `PostmortemLibrary` 與 client 只使用 canonical `postmortem_id`，配合 SD-12。

### SD-23 Agora UI

- capability enum 是 Real/Simulation/Unavailable 的唯一來源。
- candidates 由 backend `candidatePoolId` 讀取；simulated/unavailable 不進 real pool。
- suggestion 只顯示 SD-03 durable producer records。

## 11. SD-24 — BFF main assembly

此 task 是 catalog 唯一 `services/control-plane/bff/main.py` owner，但執行前必須等待外部活動 task `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` canonical merge，rebase 到其 merge commit並重建 AST parity。若 method/path/handler/source-line tuple 有任何漂移，先提交並審核 catalog amendment；不得在 implementation task 內自行重分配。

Assembly sequence：

1. shared-port consolidation 與 17 route preparation tasks全數 merge；
2. external Persona main/test owner merge；
3. clean rebase；
4. main 只保留 app factory、middleware、exception handlers、lifespan、dependency container、router includes；
5. 刪除已搬 inline handlers與 central command capability/config；
6. 驗 441 assignments、OpenAPI operation IDs、route shadowing；
7. 同進程載入兩個 app instances；
8. 跑 `tests/bff`。

任何 preparation router import `main`、複製 shared handler 或新增未列 route 都阻擋 assembly。

## 12. SD-25 — irreversible central-plane retirement

只有 SD-18 caller cutover、SD-24 main cutover與 SD-19 RuntimeBinding完成後才執行。

Delete/update set：

- `services/control-plane/internal/{__init__,internal_api,internal_api_min}.py`；
- `services/control_plane/` 下兩套 package/top-level import shims；
- `services/runtime-manager/internal_api_routes.py` 與 `main.py` mount；
- runtime smoke/hardening/internal-route tests；
- `.github/pantheon-stage0-matrix.json` legacy compile entry；
- BFF migration inventory legacy entry；
- `tests/run_internal_api_smoke.py` 與 exclusive central-plane tests；
- `services/runtime_auth_inbound.py` legacy inbound shim。

Retirement gate scans imports、URLs、env、Compose、CLI、drill、Stage0、runtime mounts與 test collection。Count 不為零就不得 delete/complete。四個 historical markdown及 `docs/`、`ai-task-archive/` 可依 allowlist 保留，但不能被 executable path 消費。

## 13. SD-26 — frontend assembly

唯一 owner 修改 `App.tsx`、`ManagementLayout.tsx`、`bff-v1/index.ts`：

- mount retained canonical pages；
- export sole bff-v1 transport；
- build/typecheck/production depgraph通過。

Playwright specs/helpers不屬此 task，避免 frontend source assembly 與 hosted acceptance 重疊。

## 14. SD-27 — exact candidate promotion

使用 capacity-1 `pantheon-dev`。Pre-switch evidence 必含：

- candidate FE/BFF SHA + image digest；
- served prior baseline seal；
- service/container health；
- exact `connectorId + ingestRunId + sourceId` projection；
- Paper producer heartbeat；
- 同一 correlation ID 的 signal、order、fill、position、heartbeat。

全部通過才 atomic switch；切換後 read back hosted manifest/version endpoints。任何 mismatch 使用 sealed local baseline rollback。此 unit 關閉 OP-G19/G20，不重開 OP-G03。

## 15. SD-28 與既有 OP-G14 lane — hosted acceptance split by repo

### SD-28 Pantheon backend/effect proof

- 12 loops 各自 natural stimulus、owner receipt、terminal state、same-ID Management readback。
- Source 執行 create→test→bounded one-shot→snapshot/effect readback→自動回 reconcile-only。
- Evidence 綁 exact accepted FE/BFF pair；skip/missing/fail阻擋。
- 不修改或另建 Source refresh endpoint。

### Existing `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` execute-plans desktop proof

- 不建立 `OPGAP-FE-HOSTED-E2E-ACCEPTANCE-20260830`。既有 blocked row 是 OP-G14 唯一 owner，沿用其三個 declared execute-plans evidence artifacts 與既有 dependencies。
- 目前 blocker 是 governed paper baseline bootstrap HTTP 500；只有 blocker state 改變且 SD-27 產生 accepted exact pair 後才 resume。這是 program execution gate，不改寫既有 task dependencies。
- 此 task 獨佔 authenticated Management/Agora desktop evidence；catalog FE assembly 與 Pantheon hosted task不得接管。
- 擷取 DOM、console、network/HAR；blocking error 或 fallback request 失敗。
- 與 SD-28 都使用同一 accepted pair；由 `pantheon-dev` capacity lock 序列化執行，不製造假的 canonical dependency。

## 16. Verification matrix

| Gate | 阻擋條件 |
|---|---|
| Catalog structure | 20 GAP dispositions不唯一、artifact collision、dependency不閉合 |
| Route parity | 不是 441、method+normalized-path重複、AST set不相等 |
| Handler atomicity | 任一 source handler有兩個 implementation owners |
| Shared-port boundary | 167-file baseline未 disposition、仍有 direct `domain_ports` caller、六個 duplicate files未刪、出現第三 namespace |
| Router boundary | preparation task edit/import main、出現 legacy/generic catch-all |
| Command retirement | 52 reference inventory未 disposition、active reference/mount不為 0 |
| Cross-repo task | 新 materialized task 的 canonical targetRepo/artifactRepoIds不相等或未 readback；既有 OP-G14 被重複 materialize |
| Source | 任務企圖重寫已完成 Source mechanisms |
| Hosted | exact pair不符、same-ID trace缺段、skip/missing/fail |
| Safety/full-system claim | F22/F25仍 unresolved卻宣稱全系統正常 |

## 17. Completion semantics

Plan-freeze merge只授權依 DAG執行，不代表任何產品 GAP 已修。每個 unit 需要 exact-head review與對應 evidence；retirement需要 zero scan與實際 deletion；hosted GAP只能由 accepted pair evidence關閉。沒有 Reviewer/Verified exact-head evidence的 commit不得視為 plan freeze completed。
