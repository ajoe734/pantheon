# Pantheon 全產品運作 GAP Current Disposition — 2026-08-30

## 0. 結論

2026-08-29 稽核的 OP-G01..OP-G20 是 20 個 audit roots，不等於 20 個仍 open 的 implementation gaps。以 Pantheon `1095c55bf...`、execute-plans `bd03c863...` 與 hosted manifest 重讀後：

- **OP-G03 closed**：pair `6899d0da...` 已於 `2026-08-30T06:28:46Z` accepted，FE `bd03c863...` / BFF `e7f010dc...`，pre/post-switch probes passed。
- **其餘 roots 有 18 個 active/verify catalog owners + 1 個 blocked existing owner**，各自只有一個 primary execution owner。
- Source bounded one-shot、reconcile-only、frontier、min-closes 與 Taiwan freshness 已存在；OP-G12 只有 hosted effect proof，沒有 Source implementation task。
- BFF `main.py` baseline 是 68,171 lines、1,727 top-level function/class symbols、421 handlers / 441 decorators；catalog 同時凍結 route rows、handler dispositions與 production symbol import inventory，不能再以「已是 composition-only」closeout。
- 28 個 archived terminal ACG tasks與 4 個 relevant terminal PFG hosted tasks已逐列 current-code reconcile；terminal facts保留且永不在本 plan標為 superseded。
- execute-plans 的舊 `bff/writeOverlay`、`bff-v1/writeFallback/seed/legacy` 與 dead NL paths已不存在；新 FE task只擁有 `bd03c863...` 仍存在的 production residual files，不能重建已刪 paths。
- Signed dev bridge 目前會遺失 `target_repo`；bootstrap 必須提供 authoritative materialize/readback，不能用 JSON 字段存在代替。
- Legacy command-plane retirement inventory 已涵蓋 code、imports、config、workflows、tests、top-level shims、runtime smoke/hardening 與 stage-0 matrix；歷史文字證據另有明確 non-executable allowlist。

## 1. Completion taxonomy

| State | 意義 | Implementation owner |
|---|---|---|
| `closed` | immutable hosted/source/terminal evidence 已滿足 root | 無；不得重開 |
| `active` | 仍需 code/config/migration/deletion | 有且唯一 |
| `verify` | source contract 已具備，但 exact deployed effect 未證明 | 只有 proof/evidence owner，不重寫已完成 code |
| `blocked` | 既有 canonical owner仍有效，但 recorded blocker尚未改變 | 重用既有 task；不得建立 duplicate 或改寫其 scope |
| `support` | architecture、integration、delivery 或 materialization 所需 | 可有 task，但 `gaps=[]` |

## 2. Frozen evidence

| 面向 | 直接觀察 |
|---|---|
| Pantheon dev | `1095c55bf42acc91fac18b701cd24ad5b1874438` |
| execute-plans dev | `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` |
| Hosted manifest | `deploymentState=accepted`；pair `6899d0daadb3dea2dbc3ae93456cf5818675dbd9a5c4284f676b80b5ce59c1a1`；`acceptedAt=2026-08-30T06:28:46Z` |
| Hosted exact pair | FE `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1`；BFF `e7f010dccee33185bc260d06048f09e6d2125f28` |
| Hosted posture | live + strict fallback + read-only；real writes false；emergency override false |
| BFF source | 68,171 lines；1,727 top-level function/class symbols；421 route handlers；441 explicit route decorators（含 explicit OPTIONS） |
| Route/symbol ownership | 441 decorator rows + 421 handler dispositions + production import symbol inventory；每個 route與 imported symbol均需 preserved 或 caller-migrated |
| ReadSurfaceStore | `read_store.py`仍存在（124 lines）；BFF Python source仍有 66 個 `ReadSurfaceStore` references，故舊 final-delete claim contradicted |
| Prior terminal reconciliation | 28 ACG + 4 PFG rows；每列 claim/evidence/reusable artifacts/exact residual/zero-or-one follow-up完整 |
| Frontend current truth | 10 個 old legacy/NL paths已 absent；current production mock/seed/overlay residual依 exact file split由 Bundle、Management、Integration owners處理 |
| Bridge materializer | `_task_spec`/readback 遺失 `target_repo`；literal preservation proof 尚未成立 |
| Existing overlap | 4 個其他 nonterminal tasks全部已 reconciliation；只有 Persona durable-readback 與 Main Assembly exact-overlap `main.py`，並有 dependency + pre-materialization terminal gate |

## 3. OP-G01..OP-G20 disposition

| GAP | State | Current fact | Completion boundary | Primary owner |
|---|---|---|---|---|
| OP-G01 | active | fallback research output 可宣稱 `real` 而無 admitted receipt | `real` 只來自 admitted receipt；污染資料隔離/重建 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G02 | active | PerformanceSuggestion producer 缺 natural caller/readback | telemetry/risk/decision event 產生 durable suggestion 並以 trigger ID reload，或刪除 claim | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G03 | **closed** | accepted exact pair `6899d0da...` 存在 | 保留 terminal evidence；changed-head promotion 不重開 G03 | **none** |
| OP-G04 | active | required release steps 可 fail/skip 而 aggregate outcome 不為失敗 | required failed/skipped/missing 皆 non-zero | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| OP-G05 | active | auth path 同步等待 provider readiness | session/tenant/RBAC 僅用 local authority；provider readiness cache/degrade | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G06 | active | generic CRUD 可用 local overlay 或呈現 unsupported control | enabled mutation 必有 typed durable owner；unsupported control disabled/removed | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` |
| OP-G07 | active | production graph 可達 seed/mock/overlay 與 mixed transport | `bff-v1` 唯一 transport；Rollup graph zero forbidden modules | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| OP-G08 | active | monolithic main、reverse imports、operation-ID/multi-replica defects | 12 cohesive owners cover 441 explicit assignments；one main owner mounts；inline decorators/collisions/import cycles zero | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| OP-G09 | active | Agora routers import private helpers/store 且 Workshop store authority 重複 | typed public ports；one store；private cross-router imports deleted | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G10 | active | generic fallback 與 mounted central `/api/internal/v1` plane remains | every executable/import/config/workflow/test caller cut first；implementation/shims/tests/stage-0 refs retire after zero proof | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| OP-G11 | verify | twelve-loop deployed proof 不是 mandatory exact-candidate receipt | 12 natural stimuli 各有 owner receipt、terminal state、same-ID readback | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` |
| OP-G12 | verify | Source Management 缺 hosted effect proof | current code 完成 create→test→bounded one-shot→snapshot→automatic reconcile-only；無新 Source route | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` |
| OP-G13 | active | synchronous TestClient compositions 可在 AnyIO portal deadlock | async ASGI transport + per-test deadline；timeout/skip 不得 pass | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G14 | **blocked** | existing AGC-14 已擁有 authenticated desktop matrix，但 recorded paper baseline bootstrap HTTP 500，browser proof未執行 | blocker有新證據後 resume原 task；沿用 bounded probe，不建立/改寫 duplicate | `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` |
| OP-G15 | active | capability truth 與 UI real/simulated/unavailable 顯示不一致 | backend capability 驅動 badge/admission；hard-coded pool removed | `OPGAP-FE-AGORA-WORKSHOP-20260830` |
| OP-G16 | active | forward lease 與 rollback 同時依賴 remote GitHub | bounded heartbeat grace；sealed local rollback；remote outage drill | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| OP-G17 | active | RuntimeBinding 可由 caller metadata 組裝 | Registry immutable physical projection；Runtime checksum/authority admission | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| OP-G18 | active | canonical Postmortem service 存在，但 UI/read projection 仍推導 `pm_<incident>` | canonical list/detail/restart readback 使用 `postmortem_id`；無第二 store | `OPGAP-BE-MGMT-POSTMORTEM-20260830` |
| OP-G19 | verify | Source→Agora pre-switch gate 缺 exact projection binding | latest `connectorId + ingestRunId + sourceId` 同時 match | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G20 | verify | producer health 與 paper lifecycle 不是一份 same-ID receipt | producer ready + natural signal→order→fill→position→heartbeat | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |

## 4. Architecture-support disposition

### 4.1 Route ownership

Catalog 的 `route_migration_inventory.assignments` 是唯一逐筆權威。Normalization 與 permanent uniqueness gate 相同：移除 query/trailing slash，將 `{name}` 或 `{name:type}` 改為 `{param}`。每個 row 的 source line 只是定位證據，owner 由 domain 語意與 target router 決定。

12 owners 的 decorator counts 是 `30 + 63 + 85 + 48 + 56 + 19 + 11 + 17 + 12 + 41 + 35 + 24 = 441`。存在的 assistant、Agora subrouters、research、evolution、management read-model、ranking、command-adapter、events routers 必須直接延伸；新建項目只限 auth/core/settings、personas/training、governance、capital/strategies、postmortems、runtime、deployment、incidents、integrations、control-loops 等尚無 canonical router 的 domain。

若 target 已有同 method/path registration，domain task 必須 reconcile 成單一實作；不得保留「舊 router + 新 router」雙重 semantic owner。

30 個 target routers在 catalog registry逐一標示：16 個 existing paths附 frozen blob；14 個 new paths附 exact absence與「無 cohesive canonical owner」assertion。後者仍需 Codex2做 semantic review，file absence 本身不能證明 architecture ownership。

Handler disposition 只有兩種：

- `move_as_unit`：同一 semantic handler 的全部 decorators/aliases 有一個 owner task、一個 target router；`bff_agora_research_tasks` 兩條 aliases 都到 Agora research router。
- `decompose_generic`：只允許明列的 cross-domain generic fallback。`sem_final_generic_read_alias` 的 approval 與 research/artifact paths各有 typed replacement；domain tasks 不複製/import generic dispatcher，Main Assembly等待兩方後才刪 old handler。

### 4.2 Command caller cutover

| Owner set | Scope |
|---|---|
| BFF adapter cutover | command adapters/executor/downstream monitor + 所有引用 legacy env/path 的 BFF tests |
| Main Assembly | `services/control-plane/bff/main.py` legacy status/readout |
| External caller cutover | 2 env examples、3 Compose、runtime-manager mount、deployment pipeline、2 ops scripts、admin CLI |
| Retirement delete/rewrite | legacy implementations、全部 underscore/top-level shims、runtime smoke/internal/hardening tests、stage-0 matrix、active contracts/runbooks |

Retirement gate 對 repository 掃描 forbidden env、URL、module、shim symbols。只有 catalog 明列的 non-executable historical prefixes/paths 可保留文字；該 allowlist 不得包含 executable、import、config、workflow 或 test file。

`services/control-plane/bff/command_executor.py` 保留為 typed BFF dispatch adaptation，不是 legacy central plane。

### 4.3 Source no-rework

沒有 `OPGAP-BE-SOURCE-CLEANUP-*`。既有 `services/source_ingestion/main.py` aliases 屬已接受 split/export contract，尚未被 exact caller proof 證明 obsolete。本 package 不刪也不擴大；未來若有證據，另開 reviewed cleanup，且不成為 OP-G12 或 BFF assembly dependency。

### 4.4 Development-tooling bootstrap

`OPGAP-DEVTOOL-TARGET-REPO-READBACK-20260830` 是 support task，不是 product GAP。它只改 local development bridge 與 governed status tooling，不加 product route。完成證據必須顯示 Pantheon/execute-plans 兩種 `target_repo` 都穿過 signed spec hash、canonical mutation 與 immutable readback。

### 4.5 Prior-terminal reconciliation

Catalog 的 `prior_delivery_dispositions` 完整列出 2026-08-28 architecture catalog全部 28 個 ACG terminal tasks，以及 4 個 relevant PFG hosted tasks。每個 terminal record只能是 `still_true`、`partial` 或 `contradicted`；contradicted描述的是**現在的 acceptance claim**，不會修改 archived terminal status，也不會把舊 task標成 superseded。

兩個必須顯式防止 false closeout 的現況是：

- `ACG-BFF-MAIN-CUTOVER-20260828` 的 composition-only claim與 current 68,171-line / 441-decorator / 1,727-symbol source矛盾；Main follow-up要求 route+symbol parity，mount router本身不算完成。
- `ACG-RS-FINAL-DELETE-20260828` 的 delete claim與 current `read_store.py`及 66 references矛盾；沿用已交付 inventory，全部 current callers有歸屬後才刪。

Frontend 的 old-path deletions與 dependency-graph gate仍有效；follow-ups只處理 current `bd03c863...` residuals。Source split、loop contracts/projection與 probe retry仍有效，分別只做 hosted current-effect proof或 AGC-14 reuse。

### 4.6 Current nonterminal reconciliation

- `AGORA-SOURCE-FRONTIER-RECOVERY-V1-20260829` blocked、`OPS-SOURCE-FRONTIER-SCOPE-RECOVERY-20260829` review_approved：本 plan無 Source implementation artifacts，不複製。
- `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` blocked：它是 OP-G14唯一 owner，固定 evidence artifacts與原 scope/dependencies不變；在 recorded paper baseline bootstrap HTTP 500 blocker改變前不 retry，也不 materialize新 hosted FE task。
- `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` in progress：唯一 exact overlap是 `main.py`；Main Assembly depends on it，且所在 materialization batch要求 predecessor terminal。

## 5. Mandatory deletion/transition inventory

| Target | Predecessor | Done evidence |
|---|---|---|
| generic BFF fallback adapter | typed BFF adapter registry | symbol/export/tests zero |
| legacy URL/env callers | adapters + main + external cutover | executable/import/config/workflow/test scan zero |
| kebab implementation files | repository-wide zero caller | implementation deleted |
| underscore + top-level shims | imports migrated | all shim files/exports deleted |
| runtime internal routes | mount/callers migrated | route module and internal/smoke/hardening tests deleted or rewritten |
| stage-0 references | retirement task | matrix does not compile/watch/run retired shim or smoke |
| main inline decorators | 12 domain tasks pass exact assignment parity | inline decorator count zero after include-router assembly |
| production mock/overlay | typed UI clients | Rollup graph zero forbidden modules |

## 6. Do not reopen

- 不建立 OP-G03 task。
- 不重開或 supersede 任何 prior terminal ACG/PFG task；follow-up只能引用 predecessor並擁有 observed residual。
- 不建立 `OPGAP-HOSTED-FE-ACCEPTANCE-20260830`；OP-G14重用既有 AGC-14。
- 不建立 Source source-code task。
- 不以 arbitrary line bands、generic route family、tail/catch-all owner 分配 route。
- 不建立跨 Pantheon/execute-plans artifact task。
- 不在 functional-first closure 中加入 Mobile、組織政策或 live-capital work。
