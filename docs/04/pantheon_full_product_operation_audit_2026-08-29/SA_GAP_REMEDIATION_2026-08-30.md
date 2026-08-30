# Pantheon 全產品運作 GAP Remediation — System Architecture — 2026-08-30

## 1. Architecture goal

本輪只完成 functional product closure、可部署 proof 與必要的 architecture/tooling cleanup。系統邊界如下：

```text
execute-plans desktop
  -> typed bff-v1 clients
  -> operator BFF composition root
       -> cohesive domain routers / typed ports
       -> canonical domain services and stores
       -> delivery/evidence gates

local development bridge
  -> governed task materialization/readback
  (不是 product BFF route，也不是 product runtime authority)
```

Source dev 常態維持 `reconcile_only`；provider egress 只有 manual bounded one-shot。Real capital/live broker、Mobile、EP5 governance program 與組織政策管理均不在本輪。

## 2. Frozen truth and completion model

- Pantheon baseline：`1095c55bf42acc91fac18b701cd24ad5b1874438`。
- execute-plans baseline：`bd03c863e3c2c1c64b9b7797f27cefaf84df17c1`。
- Hosted accepted pair：`6899d0da...`，FE `bd03c863...`，BFF `e7f010dc...`，accepted at `2026-08-30T06:28:46Z`。
- OP-G03 因此 closed/null owner；changed-head promotion 不擁有 G03。
- 18 個 catalog-owned active/verify GAPs各有一個 primary owner，OP-G14由 existing blocked AGC-14擁有；support tasks不共同擁有 GAP。
- Task/owner/batch counts 由 write authority、hot files、repository boundary 與 resource serialization 導出，不由數字配額導出；structural validator不得把目前衍生 count當 expected answer。
- 2026-08-28 的 28 個 ACG terminal deliveries與 4 個 relevant PFG hosted deliveries逐筆 current-code reconcile；舊 task不重開、不 supersede，current tasks只擁有 observed residual。

## 3. BFF target architecture

### 3.1 Composition root

最終 `services/control-plane/bff/main.py` 只負責：

- FastAPI app、middleware/CORS、lifespan；
- dependency/port/store construction；
- `include_router` composition；
- global exception handling/OpenAPI wiring。

它不再包含 domain route handlers、domain store logic、legacy command-plane readout，domain routers 也不得 import `main.py`。

Main Assembly 是 `main.py` sole owner。所有 domain tasks 先在不改 main 的前提下完成 router/port/test，並等待現有 `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` terminal；之後 Main Assembly 才 mount、比對、刪除 inline decorators。

`ACG-BFF-MAIN-CUTOVER-20260828` 的 composition-only terminal claim不能當 current proof：baseline `main.py`仍有 68,171 lines、1,727 top-level function/class symbols、421 handlers與 441 decorators，且 production routers/tooling仍 import named main symbols。Assembly因此同時凍結 route parity與 `main_symbol_inventory`；每個 imported symbol必須移到 stable owner或同批遷移 caller。`ACG-RS-FINAL-DELETE-20260828` 亦只保留 terminal歷史；current `read_store.py`與 66 references必須依 delivered inventory完成 caller parity後才刪。

### 3.2 Exact route ownership

Baseline 有 421 top-level handlers / 441 route decorators。Catalog 每個 decorator 都有：

```text
(method, normalized_path, handler, source_line, owner_task, target_router)
```

Normalized path 使用 permanent uniqueness gate 的規則；`source_line` 只是定位，不是 ownership key。每個 `(method, normalized_path)` 只能有一個 owner/target；每個 source handler 也必須 exactly once 出現在 handler migration dispositions。

| Domain owner | Decorators | Architecture decision |
|---|---:|---|
| BFF core | 30 | 功能修復與 assistant/auth/core/settings extraction 同 task |
| Persona/training | 63 | 新建具名 personas/training routers；Persona predecessor 行為由 assembly preserve |
| Agora/research | 85 | 功能修復與 route extraction 同 task；延伸 existing Agora subrouters/research router |
| Governance/evolution | 48 | 延伸 existing evolution router；只有 governance 缺 owner 才新建 |
| Capital/strategy | 56 | capital/strategies + existing ranking read-model router |
| Management/Postmortem | 19 | 功能修復與 management read-model/postmortem extraction 同 task |
| Command adapters | 11 | typed dispatch 與 existing command router extraction 同 task |
| RuntimeBinding | 17 | executable binding 修復與 runtime router extraction 同 task |
| Deployment reliability | 12 | release repair 與 deployment router extraction 同 task |
| Incident/events | 41 | existing events router + named incidents router |
| Tools/integrations | 35 | 無 existing canonical owner，建立 named integrations router |
| Control loops | 24 | intervention/sentinel/loop/OODA 收斂到 named control-loops router |

任何 existing equivalent route 都必須 merge/reconcile 到上表 target；不能把舊 registration 留著，再加第二份。禁止以 line span、generic route family、version bucket 或 tail/catch-all module 當 owner。

420 個 handlers 使用 `move_as_unit`，其所有 decorators 必須到同一 owner/target。`bff_agora_research_tasks` 的 `/bff/agora/research-tasks` 與 `/bff/research/tasks` 因此一起到 existing Agora research router。唯一 `decompose_generic` 是 `sem_final_generic_read_alias`：Governance 與 Research 各自建立 typed handler，任何一方都不得複製/import shared catch-all；Main Assembly等待兩方後刪除 old generic handler。

### 3.3 Store and port rules

- Router 只依賴 constructor-injected typed ports。
- Canonical store 由 domain service 擁有；BFF projection 不建立第二套 write authority。
- Durable identity 必須從 create receipt 一路傳到 list/detail/restart readback。
- Compatibility alias 只能指向同一 handler/service，不能成為第二 owner。
- Multi-replica 不得依賴 process-local singleton authority。

## 4. Domain architecture decisions

### 4.1 BFF core — OP-G05 / OP-G13

Auth session/tenant/RBAC 只使用 local authority；provider readiness 以 cached degraded surface 呈現，不阻塞 auth request。測試統一 async ASGI transport + hard deadline，timeout/skip 不算 pass。Core task 同時擁有其 30 route assignments，避免 auth/core routes 由另一個 extraction task 重寫。

### 4.2 Agora/research — OP-G01 / OP-G02 / OP-G09

- `real` 只由 admitted backend receipt 產生；fallback 是 `simulated` 或 `unavailable`。
- Telemetry/risk/decision natural event 觸發 durable PerformanceSuggestion，可用同 trigger ID reload。
- Workshop store authority 唯一；private cross-router imports 改為 public ports。
- Existing Agora subrouters 與 research router 是 inline route 的 target，不建立平行 aggregate family。

### 4.3 Management/Postmortem — OP-G18

Management projection 讀 canonical Postmortem service，沿用 `postmortem_id` 做 list/detail/restart readback；不得推導 `pm_<incident>` 或建立 local second store。對應 management/postmortem inline routes 同 task 搬入 existing read-model router 與 named postmortem router。

### 4.4 RuntimeBinding — OP-G17

Registry 產生 checksum-bound immutable physical projection；Deployment 只能引用，Runtime 拒絕缺失或 caller-authored authority fields。Official admitted snapshot 必須自然抵達 paper producer receipt。Runtime route extraction 與 binding remediation 同 task，避免不同 owner 對 runtime contract 各自演進。

### 4.5 Deployment — OP-G04 / OP-G16

Lease heartbeat 使用 bounded retry/grace；rollback 以 pre-switch sealed local baseline 為 authority，不依賴 remote availability。Required failed/skipped/missing results 都是 non-zero。Deployment route extraction 與 delivery repair 同 task；promotion 只 consume reviewed deploy scripts，不競爭 hot files。

### 4.6 Frontend — OP-G06 / OP-G07 / OP-G14 / OP-G15

- Production module graph 不可達 mock/seed/overlay/writeFallback。
- `bff-v1` 是唯一 transport；v5 只保留 DTO/pure transforms。
- Enabled mutation 必有 typed durable owner；unsupported controls disabled/removed。
- Agora capability drives real/simulated/unavailable badge 與 candidate admission。
- ACG 已刪除的 old `bff/writeOverlay`、`bff-v1/writeFallback/seed/legacy` 與 dead NL paths保持 absent；Bundle task只改 `frontend_residual_inventory` 列出的 current `bd03c863...` files，Management與`bff-v1/index.ts`各留給明列 owner。
- App shell/index 由一個 execute-plans integration task 組裝；hosted Playwright evidence重用 existing blocked `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`，不建立 duplicate。

## 5. Legacy command-plane retirement

### 5.1 Target state

```text
BFF typed command registry/executor
  -> Deployment / Governance / Runtime / Persona / Capital / Incident owner endpoints

(no PANTHEON_INTERNAL_API_URL)
(no /api/internal/v1)
(no internal_api_routes mount)
(no kebab/underscore/top-level internal_api shims)
```

`command_executor.py` 保留為 BFF typed dispatch adaptation。禁止保留或重建 central forwarding fallback。

### 5.2 Ordered cutover

1. BFF command adapter task 移轉 adapters/executor/downstream monitor，並更新所有引用 legacy env/path 的 BFF tests。
2. Main Assembly 移除 `main.py` 的 legacy status/readout。
3. External caller task 更新 2 env、3 Compose、runtime mount、deployment pipeline、2 ops scripts、admin CLI。
4. Retirement task 更新 stage-0 matrix、active contracts/runbooks；刪除 implementation、underscore/top-level shims、runtime internal/smoke/hardening tests 與 standalone smoke。
5. Repository gate 掃描 forbidden env/path/import/module。只有 machine catalog 的 non-executable historical allowlist 可留文字。

Retirement 不能與前三步平行，因為「implementation 還在」是 caller cutover 的 rollback boundary。

## 6. Source architecture

Source 沒有本輪 implementation task。既有 bounded one-shot、frontier/min-closes、Taiwan freshness、terminal scheduler 與 automatic `reconcile_only` 是 inherited baseline。

OP-G12 的 acceptance 只在 hosted backend task：

```text
create test source
  -> validate
  -> one-shot (max one tick, max 100 records)
  -> official snapshot
  -> terminal process
  -> controller reads reconcile_only
```

Evidence 記錄 egress allowlist/bounds。不得新增 recurring loop、第二 refresh endpoint 或 Source production artifacts。

## 7. Development-tooling boundary

Catalog 中寫有 `target_repo` 不等於 materializer 能保存它。Bootstrap task 必須：

- 讓 BridgeTask 明確要求 `target_repo`；
- 納入 signed `task_spec_hash`；
- canonical persist/readback immutable compare；
- isolated authoritative log 同時 materialize Pantheon 與 execute-plans samples；
- 在任何 artifact/repo conflict 前 fail closed；
- 不新增 product BFF route。

Bootstrap done 前，Batch B/C 不得 materialize。

## 8. Delivery and evidence architecture

`pantheon-dev` capacity=1，只有兩個 catalog tasks與一個 existing task consume：

```text
changed-head promotion
  -> hosted backend / Source acceptance
  -> existing AGC-14 hosted execute-plans desktop acceptance
```

Promotion 在 switch 前證明 exact candidate、Source projection triple 與 natural paper lifecycle，seal rollback baseline 後才 atomic switch。Backend evidence 屬 Pantheon；frontend Playwright evidence沿用 AGC-14既有 execute-plans artifacts。AGC-14只在 recorded blocker改變後 resume；任何 mismatch保留舊 accepted pair，不製造 success evidence。

## 9. Architecture invariants

- 20 GAP dispositions exactly once；OP-G03 closed/null owner。
- 18 catalog-owned active/verify GAP IDs在 child task `gaps` 中 exactly once；OP-G14 blocked owner是 existing AGC-14。
- 441 route rows exactly once；每個 method+normalized-path 唯一 owner/target。
- Baseline 421 handler dispositions / 441 decorator assignments；所有衍生 owners 的 decorator counts合計 441。
- No line-band/generic/catch-all route owner。
- Same-domain feature remediation 與 route extraction 不分裂。
- Every task single repository；artifact exact/prefix overlap zero。
- `dependency_tracks.keys == depends_on`；DAG acyclic。
- Bootstrap是first/sole initial batch row；後續 batches各自 ≤16、dependency-closed、atomic，並exactly-once覆蓋全部 child tasks。
- Legacy command refs 在 executable/import/config/workflow/test 中 zero；historical allowlist non-executable only。
- No Source code artifact/task。
- Hosted tasks序列化使用 capacity-one resource。
- 28 ACG + 4 PFG prior dispositions逐筆 current-code validated；terminal delivery不重開、不 supersede，follow-up至多一個且只擁有 residual。

## 10. Dispatch capacity and review boundary

At governed config `954caefa...`，non-Claude live capacity是 Antigravity 4、Antigravity2 4、Codex 2、Codex2 2。Bootstrap後的 current independent domain lanes由這四個 owners分配 4/4/2/2；owner與reviewer不同。Config變更就重新推導。

Structural checks只能證明 graph/count/ownership declarations內部一致。Codex2仍須逐 task判斷 bounded context是否cohesive、owned與excluded artifacts是否正確、acceptance是否證明 durable transition/readback、serialization是否必要、以及 new-router是否真的沒有 existing canonical owner。
